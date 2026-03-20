import fs from "node:fs";
import path from "node:path";
import { DynamicLink } from "fumadocs-core/dynamic-link";
import defaultMdxComponents from "fumadocs-ui/mdx";

// Read README.md from the repo root at build time
function getReadmeContent(): string {
  const readmePath = path.resolve(process.cwd(), "..", "README.md");
  try {
    return fs.readFileSync(readmePath, "utf-8");
  } catch {
    return "# MCP Template\n\nREADME.md not found.";
  }
}

// Simple markdown-to-HTML converter for the README
// Handles: headings, code blocks, tables, links, images, bold, inline code, lists, horizontal rules
function renderMarkdown(md: string): string {
  let html = md;

  // Remove HTML tags (badges, centered paragraphs, etc.) - render as-is
  // Actually keep them, browsers handle raw HTML fine

  // Fenced code blocks (```lang\n...\n```)
  html = html.replace(
    /```(\w*)\n([\s\S]*?)```/g,
    (_match, lang, code) =>
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
  html = html.replace(
    /^---$/gm,
    '<hr class="my-8 border-fd-border" />'
  );

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

  // Inline code (but not inside <pre> blocks)
  html = html.replace(/`([^`]+)`/g, '<code class="px-1.5 py-0.5 rounded bg-fd-secondary text-sm">$1</code>');

  // Links [text](url)
  html = html.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    '<a href="$2" class="text-fd-primary underline">$1</a>'
  );

  // Unordered lists
  html = html.replace(
    /^- (.+)$/gm,
    '<li class="ml-4 list-disc">$1</li>'
  );

  // Paragraphs (lines that aren't already HTML)
  html = html.replace(
    /^(?!<[a-z/]|$)(.+)$/gm,
    (_match, content) => {
      if (content.startsWith("<li")) return content;
      return `<p class="my-2">${content}</p>`;
    }
  );

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
      <div
        className="prose prose-neutral dark:prose-invert max-w-none"
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
