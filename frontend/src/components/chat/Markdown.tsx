import { Children, isValidElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CitationChip } from "@/components/citations/CitationChip";
import type { Citation } from "@/types/domain";

const CITE = /\{\{cite:(\d+)\}\}/g;

/** Replace {{cite:n}} markers inside text nodes with real citation chips. */
function withCitations(children: ReactNode, citations: Citation[]): ReactNode {
  return Children.map(children, (child) => {
    if (typeof child !== "string") {
      // Recurse into inline elements (bold, em, links) so markers inside them still resolve.
      if (isValidElement<{ children?: ReactNode }>(child) && child.props.children) {
        return { ...child, props: { ...child.props, children: withCitations(child.props.children, citations) } };
      }
      return child;
    }
    const parts: ReactNode[] = [];
    let last = 0;
    for (const m of child.matchAll(CITE)) {
      const n = Number(m[1]);
      if (m.index! > last) parts.push(child.slice(last, m.index));
      const citation = citations.find((c) => c.n === n);
      if (citation) parts.push(<CitationChip key={`${n}-${m.index}`} citation={citation} />);
      last = m.index! + m[0].length;
    }
    if (!parts.length) return child;
    if (last < child.length) parts.push(child.slice(last));
    return parts;
  });
}

/**
 * Mid-stream, markdown arrives half-written: "**Termination" renders as literal
 * asterisks until its closing pair lands. Balancing the open markers (and
 * dropping a half-typed citation marker) keeps the text readable while it
 * streams, instead of flashing raw syntax at the reader.
 */
function stabilize(md: string) {
  let out = md.replace(/\{\{c(i(t(e(:\d*)?)?)?)?$/, "");

  // A trailing opener with nothing after it yet ("… **") would render raw.
  out = out.replace(/(\*\*|\*|`)[ \t]*$/, "");

  // Close an open pair. The closer must not follow whitespace, or CommonMark
  // refuses to treat it as a closing delimiter and the asterisks stay visible.
  const odd = (sub: string) => (out.split(sub).length - 1) % 2 === 1;
  if (odd("`")) out = out.replace(/\s+$/, "") + "`";
  if (odd("**")) out = out.replace(/\s+$/, "") + "**";
  return out;
}

export function Markdown({
  content,
  citations = [],
  streaming = false,
}: {
  content: string;
  citations?: Citation[];
  streaming?: boolean;
}) {
  const text = streaming ? stabilize(content) : content;
  return (
    <div className="text-body leading-[1.65] text-ink">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => (
            <p className="mb-4 last:mb-0">{withCitations(children, citations)}</p>
          ),
          li: ({ children }) => (
            <li className="mb-1.5 last:mb-0">{withCitations(children, citations)}</li>
          ),
          ul: ({ children }) => <ul className="mb-4 list-disc space-y-0 pl-5 last:mb-0">{children}</ul>,
          ol: ({ children }) => <ol className="mb-4 list-decimal space-y-0 pl-5 last:mb-0">{children}</ol>,
          strong: ({ children }) => (
            <strong className="font-semibold text-ink">{children}</strong>
          ),
          h2: ({ children }) => (
            <h2 className="mb-2 mt-5 text-section font-semibold first:mt-0">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="mb-1.5 mt-4 text-ui font-semibold first:mt-0">{children}</h3>
          ),
          code: ({ children }) => (
            <code className="rounded-xs bg-sunken px-1 py-0.5 font-mono text-caption">{children}</code>
          ),
          blockquote: ({ children }) => (
            <blockquote className="mb-4 border-l-2 border-line-strong pl-3 font-serif text-ink-muted">
              {children}
            </blockquote>
          ),
          table: ({ children }) => (
            <div className="mb-4 overflow-x-auto rounded-md border border-line">
              <table className="w-full border-collapse text-ui">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-sunken">{children}</thead>,
          th: ({ children }) => (
            <th className="border-b border-line px-3 py-2 text-left text-caption font-semibold text-ink-muted">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border-b border-line px-3 py-2 align-top last:border-0">
              {withCitations(children, citations)}
            </td>
          ),
          a: ({ children, href }) => (
            <a href={href} className="text-brand-600 underline underline-offset-2 hover:text-brand-700">
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
