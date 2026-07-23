"""RFC 5322 email threading, and the ClamAV scanner protocol.

Both are Phase 5.6 hardening and both are pure enough to test without a
database — the threading rules are string manipulation, and clamd's wire
protocol can be spoken to a fake server on a loopback socket.

The threading properties that matter:

  * **The Message-ID is ours and exists before the send.** A reply must
    reference an id that already exists; a provider-assigned one is unknowable
    at compose time.
  * **References is the chain, not just the parent.** Clients thread on it, and
    `In-Reply-To` alone fragments when a middle message goes missing.
  * **Trimming keeps the root.** Dropping it severs the thread's identity.
"""

from __future__ import annotations

import asyncio
import struct

import pytest

from app.services.messaging.threading import (
    MAX_REFERENCES,
    build_references,
    domain_of,
    generate_message_id,
    normalise_message_id,
    parse_references,
    render_references,
    threading_headers,
    trim_references,
)
from app.services.storage.scanning import (
    ClamAvScanner,
    EicarSignatureScanner,
    build_scanner,
)


class TestMessageIds:
    def test_an_id_is_bracketed_and_domain_scoped(self) -> None:
        """A Message-ID whose domain does not match the sender is a weak DMARC
        signal, and there is no reason to spend that credibility."""
        generated = generate_message_id("vantage.example")
        assert generated.startswith("<")
        assert generated.endswith("@vantage.example>")

    def test_ids_are_unique(self) -> None:
        assert generate_message_id("x.example") != generate_message_id("x.example")

    def test_the_domain_comes_from_the_sender(self) -> None:
        assert domain_of("Agent@Vantage.Example") == "vantage.example"
        assert domain_of("not-an-address") == "localhost"

    def test_a_bare_id_gains_brackets(self) -> None:
        """Some senders omit them, and a bare id will not match a bracketed one."""
        assert normalise_message_id("abc@example.com") == "<abc@example.com>"
        assert normalise_message_id("<abc@example.com>") == "<abc@example.com>"

    def test_an_unusable_id_is_dropped_not_repaired(self) -> None:
        """A broken header is better absent than present and malformed."""
        assert normalise_message_id(None) is None
        assert normalise_message_id("   ") is None
        assert normalise_message_id("has spaces@example.com") is None
        assert normalise_message_id("<" + "x" * 600 + ">") is None


class TestReferences:
    def test_a_folded_header_parses(self) -> None:
        """Real senders fold across lines with every combination of whitespace."""
        header = "<a@x.com>\r\n\t<b@x.com>  <c@x.com>"
        assert parse_references(header) == ["<a@x.com>", "<b@x.com>", "<c@x.com>"]

    def test_duplicates_are_collapsed(self) -> None:
        assert parse_references("<a@x.com> <a@x.com>") == ["<a@x.com>"]

    def test_a_reply_extends_the_parents_chain(self) -> None:
        """References is the path from the root to the message being answered;
        appending the parent last is what extends it by one."""
        chain = build_references("<root@x.com> <mid@x.com>", "<parent@x.com>")
        assert chain == ["<root@x.com>", "<mid@x.com>", "<parent@x.com>"]

    def test_a_first_reply_references_only_the_parent(self) -> None:
        assert build_references(None, "<parent@x.com>") == ["<parent@x.com>"]

    def test_a_thread_with_no_parent_has_no_chain(self) -> None:
        assert build_references(None, None) == []

    def test_trimming_keeps_the_root(self) -> None:
        """Dropping the root would sever the thread's identity, and a client
        that has it cached would stop matching."""
        chain = [f"<{index}@x.com>" for index in range(40)]
        trimmed = trim_references(chain)

        assert len(trimmed) == MAX_REFERENCES
        assert trimmed[0] == "<0@x.com>"
        assert trimmed[-1] == "<39@x.com>"

    def test_a_short_chain_is_untouched(self) -> None:
        chain = ["<a@x.com>", "<b@x.com>"]
        assert trim_references(chain) == chain

    def test_a_long_reply_chain_stays_bounded(self) -> None:
        """Otherwise the header grows without bound and servers reject it."""
        chain: list[str] = []
        for index in range(50):
            chain = build_references(chain, f"<m{index}@x.com>")
        assert len(chain) <= MAX_REFERENCES

    def test_rendering_an_empty_chain_is_none(self) -> None:
        assert render_references([]) is None
        assert render_references(["<a@x.com>"]) == "<a@x.com>"


class TestHeaders:
    def test_a_new_thread_carries_only_a_message_id(self) -> None:
        headers = threading_headers(
            message_id="<new@x.com>", in_reply_to=None, references=[]
        )
        assert headers == {"Message-ID": "<new@x.com>"}

    def test_a_reply_carries_all_three(self) -> None:
        headers = threading_headers(
            message_id="<reply@x.com>",
            in_reply_to="<parent@x.com>",
            references=["<root@x.com>", "<parent@x.com>"],
        )
        assert headers["Message-ID"] == "<reply@x.com>"
        assert headers["In-Reply-To"] == "<parent@x.com>"
        assert headers["References"] == "<root@x.com> <parent@x.com>"

    def test_an_unusable_parent_id_is_omitted_rather_than_sent_broken(self) -> None:
        headers = threading_headers(
            message_id="<reply@x.com>", in_reply_to="not a valid id", references=[]
        )
        assert "In-Reply-To" not in headers


# --------------------------------------------------------------- scanning


class _FakeClamd:
    """A clamd that speaks just enough INSTREAM to test the client.

    A real socket rather than a mocked transport: the parts most likely to be
    wrong are the length-prefix framing and the zero-length terminator, and
    neither is exercised by a mock that returns a canned string.
    """

    def __init__(self, reply: bytes) -> None:
        self.reply = reply
        self.received = bytearray()
        self.server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader, writer) -> None:  # type: ignore[no-untyped-def]
        command = await reader.read(10)
        if command.startswith(b"zPING"):
            writer.write(b"PONG\0")
            await writer.drain()
            writer.close()
            return

        while True:
            header = await reader.readexactly(4)
            (length,) = struct.unpack("!I", header)
            if length == 0:
                break
            self.received += await reader.readexactly(length)

        writer.write(self.reply)
        await writer.drain()
        writer.close()


class TestEicarScanner:
    async def test_it_finds_the_test_file(self) -> None:
        eicar = (
            b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-"
            b"STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        )
        result = await EicarSignatureScanner().scan(eicar)
        assert result.verdict == "infected"
        assert result.signature == "EICAR-Test-File"

    async def test_everything_else_is_clean_which_is_the_point(self) -> None:
        """It is not antivirus. This test documents that as much as it checks it."""
        result = await EicarSignatureScanner().scan(b"a genuinely malicious payload")
        assert result.verdict == "clean"


class TestClamAvScanner:
    async def test_a_clean_verdict(self) -> None:
        server = _FakeClamd(b"stream: OK\0")
        await server.start()
        try:
            result = await ClamAvScanner("127.0.0.1", server.port, timeout=5).scan(
                b"harmless bytes"
            )
        finally:
            await server.stop()

        assert result.verdict == "clean"
        assert result.scanner == "clamav"
        # The framing worked: clamd received exactly what was sent.
        assert bytes(server.received) == b"harmless bytes"

    async def test_a_signature_is_extracted(self) -> None:
        server = _FakeClamd(b"stream: Win.Test.EICAR_HDB-1 FOUND\0")
        await server.start()
        try:
            result = await ClamAvScanner("127.0.0.1", server.port, timeout=5).scan(b"x")
        finally:
            await server.stop()

        assert result.verdict == "infected"
        assert result.signature == "Win.Test.EICAR_HDB-1"

    async def test_an_error_reply_is_failed_not_infected(self) -> None:
        """clamd returns ERROR for things like "size limit exceeded" — a file
        nobody looked at. Quarantining on it would delete a legitimate document.
        """
        server = _FakeClamd(b"INSTREAM size limit exceeded. ERROR\0")
        await server.start()
        try:
            result = await ClamAvScanner("127.0.0.1", server.port, timeout=5).scan(b"x")
        finally:
            await server.stop()

        assert result.verdict == "failed"

    async def test_an_unreachable_scanner_is_failed_not_clean(self) -> None:
        """The one substitution that would turn a scanner into a liability."""
        result = await ClamAvScanner("127.0.0.1", 1, timeout=2).scan(b"x")
        assert result.verdict == "failed"

    async def test_a_large_payload_is_chunked_intact(self) -> None:
        """The 4-byte length prefix per chunk is the part most likely to be
        wrong, and it only shows up past one chunk boundary."""
        payload = bytes(range(256)) * 1000  # 256 KB, four chunks
        server = _FakeClamd(b"stream: OK\0")
        await server.start()
        try:
            result = await ClamAvScanner("127.0.0.1", server.port, timeout=10).scan(
                payload
            )
        finally:
            await server.stop()

        assert result.verdict == "clean"
        assert bytes(server.received) == payload

    async def test_ping_reports_reachability(self) -> None:
        server = _FakeClamd(b"stream: OK\0")
        await server.start()
        try:
            assert await ClamAvScanner("127.0.0.1", server.port, timeout=5).ping()
        finally:
            await server.stop()

        assert not await ClamAvScanner("127.0.0.1", 1, timeout=2).ping()


class TestScannerSelection:
    def test_the_configured_engine_is_built(self) -> None:
        from pydantic import SecretStr

        from app.core.config import Settings

        def _settings(**kwargs: object) -> Settings:
            return Settings(  # type: ignore[call-arg]
                _env_file=None,
                JWT_SECRET=SecretStr("t" * 40),
                ENVIRONMENT="test",
                **kwargs,
            )

        assert build_scanner(_settings()).name == "eicar"
        assert build_scanner(_settings(MALWARE_SCANNER="clamav")).name == "clamav"

    def test_production_refuses_scanning_with_the_eicar_engine(self) -> None:
        """A scanner that catches nothing is worse than none, because it looks
        like protection."""
        from pydantic import SecretStr

        from app.core.config import Settings

        settings = Settings(  # type: ignore[call-arg]
            _env_file=None,
            JWT_SECRET=SecretStr("t" * 40),
            ENVIRONMENT="production",
            MALWARE_SCAN_ENABLED=True,
            MALWARE_SCANNER="eicar",
        )
        with pytest.raises(RuntimeError, match="eicar"):
            settings.assert_production_ready()
