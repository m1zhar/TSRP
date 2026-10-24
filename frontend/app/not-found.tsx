import Link from "next/link";
import { connection } from "next/server";

export default async function NotFound() {
  await connection();

  return (
    <main className="not-found-page">
      <div className="not-found-content">
        <p className="eyebrow">Page not found</p>
        <h1>We couldn’t find that page.</h1>
        <p>That address may have moved. Return to the TSRP research home.</p>
        <Link className="button button-primary" href="/">Return to TSRP</Link>
      </div>
    </main>
  );
}
