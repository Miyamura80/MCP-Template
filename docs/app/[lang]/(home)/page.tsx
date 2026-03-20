import fs from "node:fs";
import path from "node:path";
import { DynamicLink } from "fumadocs-core/dynamic-link";
import { marked } from "marked";

const REPO_SLUG =
  process.env.GITHUB_REPOSITORY ?? "Miyamura80/MCP-Template";
const GITHUB_RAW_BASE = `https://raw.githubusercontent.com/${REPO_SLUG}/main`;
const GITHUB_BLOB_BASE = `https://github.com/${REPO_SLUG}/blob/main`;

function getReadmeContent(): string {
  const readmePath = path.resolve(process.cwd(), "..", "README.md");
  try {
    return fs.readFileSync(readmePath, "utf-8");
  } catch {
    return "# MCP Template\n\nREADME.md not found.";
  }
}

function fixRelativePaths(md: string): string {
  let result = md;

  // Fix relative image paths in markdown syntax
  result = result.replace(
    /!\[([^\]]*)\]\((?!https?:\/\/)([^)]+)\)/g,
    `![$1](${GITHUB_RAW_BASE}/$2)`
  );

  // Fix relative image paths in HTML <img> tags
  result = result.replace(
    /src="(?!https?:\/\/)([^"]+)"/g,
    `src="${GITHUB_RAW_BASE}/$1"`
  );

  // Fix relative link paths in HTML <a> tags
  result = result.replace(
    /href="(?!https?:\/\/|#)([^"]+)"/g,
    `href="${GITHUB_BLOB_BASE}/$1"`
  );

  // Fix relative link paths in markdown syntax
  result = result.replace(
    /\[([^\]]+)\]\((?!https?:\/\/|#)([^)]+)\)/g,
    `[$1](${GITHUB_BLOB_BASE}/$2)`
  );

  return result;
}

export default async function HomePage({
  params,
}: {
  params: Promise<{ lang: string }>;
}) {
  const { lang } = await params;
  const readmeContent = fixRelativePaths(getReadmeContent());
  const readmeHtml = marked.parse(readmeContent) as string;

  return (
    <div className="max-w-4xl mx-auto px-6 py-12">
      <style
        dangerouslySetInnerHTML={{
          __html: `
            .readme-content, .readme-content * {
              color: var(--color-fd-foreground) !important;
            }
            .readme-content a, .readme-content a * {
              color: var(--color-fd-accent-foreground) !important;
              text-decoration: underline;
            }
            .readme-content h1 { font-size: 1.875rem; font-weight: 700; margin-top: 2rem; margin-bottom: 1rem; }
            .readme-content h2 { font-size: 1.5rem; font-weight: 700; margin-top: 2.5rem; margin-bottom: 1rem; }
            .readme-content h3 { font-size: 1.25rem; font-weight: 600; margin-top: 2rem; margin-bottom: 0.75rem; }
            .readme-content p { margin: 0.5rem 0; }
            .readme-content img { max-width: 100%; border-radius: 0.5rem; margin: 1rem 0; }
            .readme-content pre {
              padding: 1rem;
              border-radius: 0.5rem;
              background-color: var(--color-fd-secondary) !important;
              overflow-x: auto;
              margin: 1rem 0;
            }
            .readme-content pre code, .readme-content pre * {
              color: var(--color-fd-foreground) !important;
              font-size: 0.875rem;
            }
            .readme-content :not(pre) > code {
              padding: 0.125rem 0.375rem;
              border-radius: 0.25rem;
              background-color: var(--color-fd-secondary);
              font-size: 0.875rem;
            }
            .readme-content table { width: 100%; border-collapse: collapse; margin: 1rem 0; }
            .readme-content th, .readme-content td {
              border: 1px solid var(--color-fd-border);
              padding: 0.5rem 0.75rem;
              text-align: left;
            }
            .readme-content th { font-weight: 500; }
            .readme-content ul { margin-left: 1rem; list-style-type: disc; margin-top: 0.5rem; margin-bottom: 0.5rem; }
            .readme-content ol { margin-left: 1rem; list-style-type: decimal; margin-top: 0.5rem; margin-bottom: 0.5rem; }
            .readme-content li { margin: 0.25rem 0; }
            .readme-content hr { margin: 2rem 0; border-color: var(--color-fd-border); }
            .readme-content blockquote {
              border-left: 4px solid var(--color-fd-border);
              padding-left: 1rem;
              margin: 1rem 0;
              color: var(--color-fd-muted-foreground) !important;
            }
            .readme-content blockquote * {
              color: var(--color-fd-muted-foreground) !important;
            }
          `,
        }}
      />
      <div
        className="readme-content max-w-none"
        dangerouslySetInnerHTML={{ __html: readmeHtml }}
      />
      <div className="mt-12 text-center">
        <DynamicLink
          href="/[lang]/docs"
          className="inline-flex items-center gap-2 px-6 py-3 rounded-lg bg-fd-primary text-fd-primary-foreground font-medium hover:opacity-90 transition-opacity"
        >
          View Documentation
        </DynamicLink>
      </div>
    </div>
  );
}
