import { Suspense } from "react";
import { ResearchWorkspace } from "@/components/research-workspace";

function Shell() {
  return <main className="page-shell"><div className="loading-bar" aria-label="Loading TSRP" /></main>;
}

export default function Home() {
  return <Suspense fallback={<Shell />}><ResearchWorkspace /></Suspense>;
}
