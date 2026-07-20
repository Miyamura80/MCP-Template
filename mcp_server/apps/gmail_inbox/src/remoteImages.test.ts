import { describe, expect, it } from "vitest";
import {
  BLOCKED_IMG_PLACEHOLDER,
  blockRemoteImages,
  restoreRemoteImages,
} from "./remoteImages";

describe("blockRemoteImages", () => {
  it("replaces remote img src with a placeholder and records the URL", () => {
    const { html, remoteUrls } = blockRemoteImages(
      '<p>hi</p><img src="https://cdn.example.com/logo.png" alt="logo">',
    );
    expect(remoteUrls).toEqual(["https://cdn.example.com/logo.png"]);
    expect(html).toContain(`src="${BLOCKED_IMG_PLACEHOLDER}"`);
    expect(html).toContain('data-remote-src="https://cdn.example.com/logo.png"');
    // The live src attribute must be gone (data-remote-src may remain).
    expect(html).not.toMatch(/\ssrc="https:\/\/cdn\.example\.com\/logo\.png"/);
  });

  it("leaves data: and cid: image srcs untouched", () => {
    const input =
      '<img src="data:image/gif;base64,R0lGOD"><img src="cid:inline-1">';
    const { html, remoteUrls } = blockRemoteImages(input);
    expect(remoteUrls).toEqual([]);
    expect(html).toContain('src="data:image/gif;base64,R0lGOD"');
    expect(html).toContain('src="cid:inline-1"');
    expect(html).not.toContain("data-remote-src");
  });

  it("dedupes repeated URLs but blocks every occurrence", () => {
    const url = "https://t.example.com/pixel.gif";
    const { html, remoteUrls } = blockRemoteImages(
      `<img src="${url}"><img src="${url}">`,
    );
    expect(remoteUrls).toEqual([url]);
    expect(html.match(/data-remote-src/g)).toHaveLength(2);
  });

  it("drops remote srcset and neutralizes legacy background attributes", () => {
    const { html, remoteUrls } = blockRemoteImages(
      '<img src="https://a.example/x.png" srcset="https://a.example/x2.png 2x">' +
        '<table background="https://a.example/bg.png"><tr><td>x</td></tr></table>',
    );
    expect(html).not.toContain("srcset");
    // The bare attribute must be gone (data-remote-background may remain).
    expect(html).not.toMatch(/<table[^>]* background=/);
    expect(html).toContain('data-remote-background="https://a.example/bg.png"');
    expect(remoteUrls).toEqual([
      "https://a.example/x.png",
      "https://a.example/bg.png",
    ]);
  });

  it("scrubs remote url() references from inline styles", () => {
    const { html } = blockRemoteImages(
      "<div style=\"color:red;background-image:url('https://a.example/bg.jpg')\">x</div>",
    );
    expect(html).not.toContain("https://a.example/bg.jpg");
    expect(html).toContain("color:red");
  });
});

describe("restoreRemoteImages", () => {
  it("swaps resolved URLs in as fetched data URIs", () => {
    const url = "https://cdn.example.com/logo.png";
    const { html } = blockRemoteImages(`<img src="${url}">`);
    const restored = restoreRemoteImages(
      html,
      new Map([[url, "data:image/png;base64,AAAA"]]),
    );
    expect(restored).toContain('src="data:image/png;base64,AAAA"');
    expect(restored).not.toContain("data-remote-src");
  });

  it("keeps the placeholder for URLs the server could not fetch", () => {
    const { html } = blockRemoteImages(
      '<img src="https://a.example/ok.png"><img src="https://a.example/broken.png">',
    );
    const restored = restoreRemoteImages(
      html,
      new Map([["https://a.example/ok.png", "data:image/png;base64,AAAA"]]),
    );
    expect(restored).toContain('src="data:image/png;base64,AAAA"');
    expect(restored).toContain(`src="${BLOCKED_IMG_PLACEHOLDER}"`);
    expect(restored).toContain('data-remote-src="https://a.example/broken.png"');
  });

  it("restores legacy background attributes", () => {
    const url = "https://a.example/bg.png";
    const { html } = blockRemoteImages(`<table background="${url}"><tr><td>x</td></tr></table>`);
    const restored = restoreRemoteImages(
      html,
      new Map([[url, "data:image/png;base64,BBBB"]]),
    );
    expect(restored).toContain('background="data:image/png;base64,BBBB"');
  });
});
