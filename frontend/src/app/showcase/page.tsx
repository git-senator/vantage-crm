import type { Metadata } from "next";

import { fetchListings, type ShowcaseListing } from "@/lib/showcase";

import { EnquiryForm } from "./enquiry-form";
import { Reveal } from "./reveal";
import styles from "./showcase.module.css";

/**
 * The public catalogue.
 *
 * Server-rendered end to end, and it has to be: the photo URLs are signed and
 * short-lived, so they are minted as the HTML is built. A statically cached
 * page would serve dead images an hour later.
 *
 * Two languages, chosen by `?lang=`. English leads because the buyers this is
 * aimed at are in the United States and Canada; Portuguese is there for the
 * local market. Russian exists in the data but is not offered here — the
 * Russian-speaking audience already has rossagroupbrazil.com.
 */

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "The Rossa Group — Brazilian real estate",
  description:
    "Ocean-front apartments and villas in Santa Catarina, bought remotely, held legally, and let for income.",
};

type Lang = "en" | "pt-BR";

const COPY = {
  en: {
    eyebrow: "Santa Catarina · Brazil",
    title: "Property in Brazil, chosen the way you would choose it yourself",
    lede: "We buy, verify and manage ocean-front apartments and villas for owners who live somewhere else. Seven years on this coast, a Brazilian licence, and every transaction under legal review.",
    cta: "Speak to us",
    browse: "See the collection",
    collectionTitle: "The current collection",
    collectionNote:
      "Every listing is one we have walked through. Prices and terms are confirmed with the developer before we publish them.",
    albumTitle: "Every room, before you fly",
    albumNote:
      "Photographs of the properties themselves — not renders, not stock. This is what you will be shown when you arrive.",
    formTitle: "Tell us what you are looking for",
    formNote:
      "One of us replies personally, in your language. No mailing list, no automated follow-ups.",
    facts: [
      { n: "6–8%", l: "annual yield on long-term rental" },
      { n: "10–12%", l: "on short-term rental" },
      { n: "4 years", l: "interest-free instalments available" },
      { n: "CRECI PJ8962-J", l: "licensed Brazilian brokerage" },
    ],
    form: {
      name: "First name",
      surname: "Last name",
      email: "Email",
      phone: "Phone",
      message: "What are you looking for?",
      submit: "Send",
      sending: "Sending…",
      thanks: "Thank you — we will be in touch shortly.",
      failed: "Something went wrong. Please try again, or write to us directly.",
      note: "We use these details only to answer you.",
    },
    beds: "beds",
    baths: "baths",
    photos: "photos",
    onRequest: "On request",
  },
  "pt-BR": {
    eyebrow: "Santa Catarina · Brasil",
    title: "Imóveis no Brasil, escolhidos como você mesmo escolheria",
    lede: "Compramos, verificamos e administramos apartamentos e casas de frente para o mar para proprietários que moram em outro país. Sete anos nesta costa, licença brasileira e cada negociação sob análise jurídica.",
    cta: "Fale conosco",
    browse: "Ver a coleção",
    collectionTitle: "A coleção atual",
    collectionNote:
      "Cada imóvel foi visitado por nós. Preços e condições são confirmados com a construtora antes de publicarmos.",
    albumTitle: "Cada ambiente, antes da viagem",
    albumNote:
      "Fotografias dos imóveis reais — não são renders nem banco de imagens. É o que você verá quando chegar.",
    formTitle: "Conte o que você procura",
    formNote:
      "Um de nós responde pessoalmente, no seu idioma. Sem lista de e-mails, sem mensagens automáticas.",
    facts: [
      { n: "6–8%", l: "rentabilidade anual na locação longa" },
      { n: "10–12%", l: "na locação por temporada" },
      { n: "4 anos", l: "parcelamento sem juros disponível" },
      { n: "CRECI PJ8962-J", l: "imobiliária licenciada no Brasil" },
    ],
    form: {
      name: "Nome",
      surname: "Sobrenome",
      email: "E-mail",
      phone: "Telefone",
      message: "O que você procura?",
      submit: "Enviar",
      sending: "Enviando…",
      thanks: "Obrigado — entraremos em contato em breve.",
      failed: "Algo deu errado. Tente novamente ou escreva diretamente.",
      note: "Usamos estes dados apenas para responder a você.",
    },
    beds: "quartos",
    baths: "banheiros",
    photos: "fotos",
    onRequest: "Sob consulta",
  },
} as const;

/** The infinity-and-keys mark, drawn rather than fetched so it inherits gold. */
function Mark() {
  return (
    <svg
      className={styles.mark}
      viewBox="0 0 48 48"
      role="img"
      aria-label="The Rossa Group"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
    >
      <path d="M14 24c0-4.4 3.1-8 7-8s7 3.6 7 8-3.1 8-7 8-7-3.6-7-8Z" />
      <path d="M20 24c0-4.4 3.1-8 7-8s7 3.6 7 8-3.1 8-7 8-7-3.6-7-8Z" />
      <path d="M24 6v4M24 38v4" strokeLinecap="round" />
    </svg>
  );
}

function price(listing: ShowcaseListing, fallback: string): string {
  if (!listing.price || Number(listing.price) <= 0) return fallback;
  const value = Number(listing.price);
  const currency = listing.currency || "USD";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(value);
}

function place(listing: ShowcaseListing): string {
  return [listing.city, listing.state].filter(Boolean).join(" · ");
}

export default async function ShowcasePage({
  searchParams,
}: {
  searchParams: Promise<{ lang?: string }>;
}) {
  const { lang } = await searchParams;
  const locale: Lang = lang === "pt-BR" || lang === "pt" ? "pt-BR" : "en";
  const t = COPY[locale];

  const listings = await fetchListings(locale, 60);

  // The arc takes the best cover shots; more than nine and the outer cards are
  // turned so far they read as noise.
  const arc = listings.filter((l) => l.cover_url).slice(0, 9);
  const centre = (arc.length - 1) / 2;

  // The album is every cover we have, tiled. Full galleries per listing would
  // be several hundred signed URLs on one page — that belongs on the listing's
  // own page, which is the next piece of work.
  const album = listings.filter((l) => l.cover_url);

  return (
    <div className={styles.page}>
      <div className={styles.sky} aria-hidden="true">
        <div className={`${styles.cloud} ${styles.cloud1}`} />
        <div className={`${styles.cloud} ${styles.cloud2}`} />
        <div className={`${styles.cloud} ${styles.cloud3}`} />
        <div className={`${styles.cloud} ${styles.cloud4}`} />
      </div>

      <div className={styles.content}>
        <header className={styles.bar}>
          <div className={styles.brand}>
            <Mark />
            <span>The Rossa Group</span>
          </div>
          <nav className={styles.langs}>
            <a
              className={`${styles.lang} ${locale === "en" ? styles.langOn : ""}`}
              href="?lang=en"
            >
              EN
            </a>
            <a
              className={`${styles.lang} ${locale === "pt-BR" ? styles.langOn : ""}`}
              href="?lang=pt-BR"
            >
              PT
            </a>
          </nav>
        </header>

        <section className={styles.hero}>
          <p className={styles.eyebrow}>{t.eyebrow}</p>
          <h1 className={styles.title}>{t.title}</h1>
          <p className={styles.lede}>{t.lede}</p>
          <div className={styles.heroActions}>
            <a className={styles.cta} href="#enquiry">
              {t.cta}
            </a>
            <a className={styles.ghost} href="#collection">
              {t.browse}
            </a>
          </div>

          <div className={styles.arcWrap}>
            <div className={styles.arc}>
              {arc.map((listing, index) => {
                // Each card sits on the surface of a cylinder: a share of the
                // sweep as rotation, and a push back along Z so the turn reads
                // as depth rather than a skew.
                const offset = index - centre;
                const angle = offset * 7.5;
                const depth = -Math.abs(offset) * 34;
                const lift = Math.abs(offset) * 10;
                return (
                  <div
                    key={listing.id}
                    className={styles.arcItem}
                    style={{
                      transform: `rotateY(${-angle}deg) translateZ(${depth}px) translateY(${lift}px)`,
                    }}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={listing.cover_url ?? ""}
                      alt={listing.title}
                      loading={index < 5 ? "eager" : "lazy"}
                    />
                  </div>
                );
              })}
            </div>
          </div>
        </section>

        <section className={styles.section} id="collection">
          <Reveal>
            <div className={styles.sectionHead}>
              <h2 className={styles.h2}>{t.collectionTitle}</h2>
              <p className={styles.sectionNote}>{t.collectionNote}</p>
            </div>
          </Reveal>

          <div className={styles.grid}>
            {listings.map((listing, index) => (
              <Reveal key={listing.id} delay={Math.min(index, 6) * 60}>
                <article className={styles.card}>
                  <div className={styles.cardPhoto}>
                    {listing.cover_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={listing.cover_url}
                        alt={listing.title}
                        loading="lazy"
                      />
                    ) : null}
                  </div>
                  <div className={styles.cardBody}>
                    <div className={styles.cardTop}>
                      <h3 className={styles.cardName}>{listing.title}</h3>
                      <span className={styles.cardPrice}>
                        {price(listing, t.onRequest)}
                      </span>
                    </div>
                    <p className={styles.cardPlace}>{place(listing)}</p>
                    <div className={styles.specs}>
                      {listing.area_m2 ? <span>{listing.area_m2} m²</span> : null}
                      {listing.bedrooms ? (
                        <span>
                          {listing.bedrooms} {t.beds}
                        </span>
                      ) : null}
                      {listing.bathrooms ? (
                        <span>
                          {Number(listing.bathrooms)} {t.baths}
                        </span>
                      ) : null}
                      {listing.photo_count ? (
                        <span>
                          {listing.photo_count} {t.photos}
                        </span>
                      ) : null}
                    </div>
                  </div>
                </article>
              </Reveal>
            ))}
          </div>
        </section>

        <section className={styles.section}>
          <Reveal>
            <div className={styles.sectionHead}>
              <h2 className={styles.h2}>{t.albumTitle}</h2>
              <p className={styles.sectionNote}>{t.albumNote}</p>
            </div>
          </Reveal>
          <Reveal>
            <div className={styles.album}>
              {album.map((listing) => (
                <div key={`album-${listing.id}`} className={styles.albumItem}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={listing.cover_url ?? ""}
                    alt={listing.title}
                    loading="lazy"
                  />
                </div>
              ))}
            </div>
          </Reveal>
        </section>

        <section className={styles.section} id="enquiry">
          <Reveal>
            <div className={styles.sectionHead}>
              <h2 className={styles.h2}>{t.formTitle}</h2>
              <p className={styles.sectionNote}>{t.formNote}</p>
            </div>
            <div className={styles.formWrap}>
              <EnquiryForm copy={t.form} locale={locale} />
              <div className={styles.facts}>
                {t.facts.map((fact) => (
                  <div key={fact.l} className={styles.fact}>
                    <span className={styles.factNum}>{fact.n}</span>
                    <span className={styles.factLabel}>{fact.l}</span>
                  </div>
                ))}
              </div>
            </div>
          </Reveal>
        </section>

        <footer className={styles.footer}>
          <span>© {new Date().getFullYear()} The Rossa Group · CRECI PJ8962-J</span>
          <span>
            <a href="tel:+5511968422222">+55 (11) 96842-2222</a>
            {" · "}
            <a href="tel:+5548992116579">+55 (48) 99211-6579</a>
          </span>
        </footer>
      </div>
    </div>
  );
}
