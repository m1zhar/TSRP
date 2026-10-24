import type { DefaultSession } from "next-auth";

declare module "next-auth" {
  interface Session {
    user?: DefaultSession["user"] & { authenticated: true; browserStorageScope: string };
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    authenticated?: boolean;
    authTime?: number;
    browserStorageScope?: string;
  }
}
