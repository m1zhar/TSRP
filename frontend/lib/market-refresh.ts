export const MARKET_LIST_REFRESH_INTERVAL_MINUTES = 5;
export const MARKET_LIST_REFRESH_INTERVAL_MS = MARKET_LIST_REFRESH_INTERVAL_MINUTES * 60_000;

export type MarketListKind = "movers" | "stocks" | "popular";

export const MARKET_LIST_LABELS = {
  movers: "Top movers",
  stocks: "Most active",
  popular: "Popular stocks",
} as const satisfies Record<MarketListKind, string>;

export function marketListEndpoint(kind: MarketListKind): "/api/movers" | "/api/top_stocks" | "/api/top_stocks?list=popular" {
  if (kind === "movers") return "/api/movers";
  return kind === "popular" ? "/api/top_stocks?list=popular" : "/api/top_stocks";
}

export function shouldPollMarketList(visibility: string): boolean {
  return visibility === "visible";
}
