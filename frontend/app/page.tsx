import { connection } from "next/server";
import { ResearchWorkspace } from "@/components/research-workspace";

export default async function Home() {
  await connection();
  return <ResearchWorkspace />;
}
