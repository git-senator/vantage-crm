"use client";

import { useState } from "react";

import styles from "./showcase.module.css";

interface Copy {
  name: string;
  surname: string;
  email: string;
  phone: string;
  message: string;
  submit: string;
  sending: string;
  thanks: string;
  failed: string;
  note: string;
}

/**
 * The enquiry form.
 *
 * It posts to a route handler on our own origin rather than to the API
 * directly: the browser then never learns where the API lives, and the request
 * carries no CORS preflight. The handler is the only piece that knows both.
 *
 * The success state replaces the form rather than sitting above it. A form
 * still standing there after a successful send invites a second submission,
 * and two identical leads are worse than none — an agent has to work out which
 * is real.
 */
export function EnquiryForm({
  copy,
  locale,
  listingId,
}: {
  copy: Copy;
  locale: string;
  listingId?: string;
}) {
  const [state, setState] = useState<"idle" | "sending" | "done" | "error">(
    "idle",
  );

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (state === "sending") return;

    const data = new FormData(event.currentTarget);
    setState("sending");
    try {
      const response = await fetch("/api/showcase/enquiry", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          first_name: String(data.get("first_name") ?? "").trim(),
          last_name: String(data.get("last_name") ?? "").trim() || null,
          email: String(data.get("email") ?? "").trim() || null,
          phone: String(data.get("phone") ?? "").trim() || null,
          message: String(data.get("message") ?? "").trim() || null,
          listing_id: listingId ?? null,
          locale,
        }),
      });
      setState(response.ok ? "done" : "error");
    } catch {
      setState("error");
    }
  }

  if (state === "done") {
    return <p className={styles.ok}>{copy.thanks}</p>;
  }

  return (
    <form className={styles.form} onSubmit={onSubmit}>
      <div className={styles.row}>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="first_name">
            {copy.name}
          </label>
          <input
            className={styles.input}
            id="first_name"
            name="first_name"
            required
            maxLength={100}
            autoComplete="given-name"
          />
        </div>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="last_name">
            {copy.surname}
          </label>
          <input
            className={styles.input}
            id="last_name"
            name="last_name"
            maxLength={100}
            autoComplete="family-name"
          />
        </div>
      </div>

      <div className={styles.row}>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="email">
            {copy.email}
          </label>
          <input
            className={styles.input}
            id="email"
            name="email"
            type="email"
            maxLength={255}
            autoComplete="email"
          />
        </div>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="phone">
            {copy.phone}
          </label>
          <input
            className={styles.input}
            id="phone"
            name="phone"
            type="tel"
            maxLength={40}
            autoComplete="tel"
          />
        </div>
      </div>

      <div className={styles.field}>
        <label className={styles.label} htmlFor="message">
          {copy.message}
        </label>
        <textarea
          className={styles.textarea}
          id="message"
          name="message"
          maxLength={2000}
        />
      </div>

      <button
        className={styles.submit}
        type="submit"
        disabled={state === "sending"}
      >
        {state === "sending" ? copy.sending : copy.submit}
      </button>

      {state === "error" ? <p className={styles.err}>{copy.failed}</p> : null}
      <p className={styles.note}>{copy.note}</p>
    </form>
  );
}
