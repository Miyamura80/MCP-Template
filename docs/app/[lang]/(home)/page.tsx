import fs from "node:fs";
import path from "node:path";
import { DynamicLink } from "fumadocs-core/dynamic-link";

const GITHUB_RAW_BASE =
  "https://raw.githubusercontent.com/Miyamura80/MCP-Template/main";

function getReadmeContent(): string {
  const readmePath = path.resolve(process.cwd(), "..", "README.md");
  try {
    return fs.readFileSync(readmePath, "utf-8");
  } catch {
    return "# MCP Template\n\nREADME.md not found.";
  }
}

function renderMarkdown(md: string): string {
  let html = md;

  // Fix relative image paths in markdown syntax
  html = html.replace(
    /!\[([^\]]*)\]\((?!https?:\/\/)([^)]+)\)/g,
    `![$1](${GITHUB_RAW_BASE}/$2)`
  );

  // Fix relative image paths in HTML <img> tags
  html = html.replace(
    /src="(?!https?:\/\/)([^"]+)"/g,
    `src="${GITHUB_RAW_BASE}/$1"`
  );

  // Fix relative link paths in HTML <a> tags
  html = html.replace(
    /href="(?!https?:\/\/|#)([^"]+)"/g,
    `href="https://github.com/Miyamura80/MCP-Template/blob/main/$1"`
  );

  // Fenced code blocks
  html = html.replace(
    /```(\w*)\n([\s\S]*?)```/g,
    (_match, _lang, code) =>
      `<pre class="p-4 rounded-lg bg-fd-secondary overflow-x-auto my-4"><code class="text-sm">${code
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")}</code></pre>`
  );

  // Headings
  html = html.replace(
    /^#### (.+)$/gm,
    '<h4 class="text-lg font-semibold mt-6 mb-2">$1</h4>'
  );
  html = html.replace(
    /^### (.+)$/gm,
    '<h3 class="text-xl font-semibold mt-8 mb-3">$1</h3>'
  );
  html = html.replace(
    /^## (.+)$/gm,
    '<h2 class="text-2xl font-bold mt-10 mb-4">$1</h2>'
  );
  html = html.replace(
    /^# (.+)$/gm,
    '<h1 class="text-3xl font-bold mt-8 mb-4">$1</h1>'
  );

  // Horizontal rules
  html = html.replace(/^---$/gm, '<hr class="my-8 border-fd-border" />');

  // Tables
  html = html.replace(
    /^\|(.+)\|\n\|[-| :]+\|\n((?:\|.+\|\n?)*)/gm,
    (_match, headerRow, bodyRows) => {
      const headers = headerRow
        .split("|")
        .map((h: string) => h.trim())
        .filter(Boolean);
      const rows = bodyRows
        .trim()
        .split("\n")
        .map((row: string) =>
          row
            .split("|")
            .map((c: string) => c.trim())
            .filter(Boolean)
        );
      const thead = headers
        .map(
          (h: string) =>
            `<th class="border border-fd-border px-3 py-2 text-left font-medium">${h}</th>`
        )
        .join("");
      const tbody = rows
        .map(
          (row: string[]) =>
            `<tr>${row
              .map(
                (c: string) =>
                  `<td class="border border-fd-border px-3 py-2">${c}</td>`
              )
              .join("")}</tr>`
        )
        .join("");
      return `<div class="overflow-x-auto my-4"><table class="w-full border-collapse"><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table></div>`;
    }
  );

  // Bold
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  // Inline code
  html = html.replace(
    /`([^`]+)`/g,
    '<code class="px-1.5 py-0.5 rounded bg-fd-secondary text-sm">$1</code>'
  );

  // Markdown links [text](url)
  html = html.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    '<a href="$2" class="text-fd-accent-foreground underline hover:opacity-80">$1</a>'
  );

  // Images ![alt](src)
  html = html.replace(
    /!\[([^\]]*)\]\(([^)]+)\)/g,
    '<img src="$2" alt="$1" class="max-w-full rounded-lg my-4" />'
  );

  // Unordered lists
  html = html.replace(
    /^- (.+)$/gm,
    '<li class="ml-4 list-disc">$1</li>'
  );

  // Paragraphs
  html = html.replace(/^(?!<[a-z/]|$)(.+)$/gm, (_match, content) => {
    if (content.startsWith("<li")) return content;
    return `<p class="my-2">${content}</p>`;
  });

  return html;
}

export default async function HomePage({
  params,
}: {
  params: Promise<{ lang: string }>;
}) {
  const { lang } = await params;
  const readmeContent = getReadmeContent();
  const readmeHtml = renderMarkdown(readmeContent);

  return (
    <div className="max-w-4xl mx-auto px-6 py-12">
      <style
        dangerouslySetInnerHTML={{
          __html: `
            .readme-content a { color: var(--color-fd-accent-foreground); text-decoration: underline; }
            .readme-content a:hover { opacity: 0.8; }
            .readme-content img { max-width: 100%; border-radius: 0.5rem; }
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
