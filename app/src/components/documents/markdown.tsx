"use client";

import ReactMarkdown from "react-markdown";

// Markdown for a document: raw HTML is never parsed (react-markdown's default),
// images are refused (a picture is an Image, through custody), links open
// outside and carry no referrer. Anything but http(s) and mailto is dropped.
export const SAFE_LINK = /^(https?:|mailto:)/i;

export function Md({ text }: { text: string }) {
  return (
    <div className="flex flex-col gap-3 text-body text-ink [&_code]:rounded-control [&_code]:bg-hoodie [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-meta [&_ol]:list-decimal [&_ol]:pl-5 [&_ul]:list-disc [&_ul]:pl-5 [&_li]:my-0.5 [&_h1]:text-heading [&_h2]:text-lead [&_h3]:text-title [&_h1]:font-semibold [&_h2]:font-semibold [&_h3]:font-semibold [&_blockquote]:border-l-2 [&_blockquote]:border-hairline [&_blockquote]:pl-3 [&_blockquote]:text-ink-soft">
      <ReactMarkdown
        disallowedElements={["img", "iframe", "script", "style"]}
        unwrapDisallowed
        urlTransform={(url) => (SAFE_LINK.test(url) ? url : "")}
        components={{
          a: ({ href, children }) =>
            href ? (
              <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-denim underline underline-offset-2">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
