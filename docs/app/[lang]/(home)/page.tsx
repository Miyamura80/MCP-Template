import fs from "node:fs";
import path from "node:path";
import { DynamicLink } from "fumadocs-core/dynamic-link";
import { marked } from "marked";

const GITHUB_RAW_BASE =
  "https://raw.githubusercontent.com/Miyamura80/MCP-Template/main";
const GITHUB_BLOB_BASE =
  "https://github.com/Miyamura80/MCP-Template/blob/main";

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
    <div className="max-w-4xl mx-auto px-6 py-12 text-fd-foreground">
      <div
        className="readme-content max-w-none text-fd-foreground
          [&_*]:text-fd-foreground
          [&_h1]:text-3xl [&_h1]:font-bold [&_h1]:mt-8 [&_h1]:mb-4
          [&_h2]:text-2xl [&_h2]:font-bold [&_h2]:mt-10 [&_h2]:mb-4
          [&_h3]:text-xl [&_h3]:font-semibold [&_h3]:mt-8 [&_h3]:mb-3
          [&_p]:my-2
          [&_a]:!text-fd-accent-foreground [&_a]:underline
          [&_img]:max-w-full [&_img]:rounded-lg [&_img]:my-4
          [&_pre]:p-4 [&_pre]:rounded-lg [&_pre]:bg-fd-secondary [&_pre]:overflow-x-auto [&_pre]:my-4
          [&_code]:text-sm
          [&_:not(pre)>code]:px-1.5 [&_:not(pre)>code]:py-0.5 [&_:not(pre)>code]:rounded [&_:not(pre)>code]:bg-fd-secondary
          [&_table]:w-full [&_table]:border-collapse [&_table]:my-4
          [&_th]:border [&_th]:border-fd-border [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-medium
          [&_td]:border [&_td]:border-fd-border [&_td]:px-3 [&_td]:py-2
          [&_ul]:ml-4 [&_ul]:list-disc [&_ul]:my-2
          [&_ol]:ml-4 [&_ol]:list-decimal [&_ol]:my-2
          [&_li]:my-1
          [&_hr]:my-8 [&_hr]:border-fd-border
          [&_blockquote]:border-l-4 [&_blockquote]:border-fd-border [&_blockquote]:pl-4 [&_blockquote]:my-4 [&_blockquote]:!text-fd-muted-foreground"
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
