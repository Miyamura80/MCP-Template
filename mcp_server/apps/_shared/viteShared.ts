// Plain-object pieces of every app's vite.config.ts. No imports: see README.

// Apps import ../_shared by relative path; let the dev server serve it.
export const sharedServer = { fs: { allow: [".."] } };

// ../_shared has no package of its own, so every app runs its tests too.
export const sharedTestInclude = ["src/**/*.test.{ts,tsx}", "../_shared/**/*.test.ts"];
