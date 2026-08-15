"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";

import { api } from "@/lib/api";
import { AuthShell } from "@/components/shell/auth-shell";
import { ErrorState, Skeleton } from "@/components/ui/feedback";

interface LegalDocument {
  slug: string;
  title: string;
  version: string;
  body: string;
  is_placeholder: boolean;
}

/**
 * A policy document, readable without an account — nobody can agree to something they cannot read.
 *
 * The Markdown is rendered with a deliberately small formatter rather than a library: these are
 * two documents with headings, paragraphs and lists, and pulling in a Markdown parser to render
 * them would be more surface area than the job needs. Nothing is set as HTML.
 */
export default function LegalPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);

  const document = useQuery({
    queryKey: ["legal", slug],
    queryFn: () => api.get<LegalDocument>(`/api/v1/legal/${slug}`),
  });

  return (
    <AuthShell title={document.data?.title ?? "Legal"}>
      {document.isError ? (
        <ErrorState error={document.error} onRetry={() => void document.refetch()} />
      ) : document.isLoading || !document.data ? (
        <Skeleton className="h-64 rounded" />
      ) : (
        <article className="space-y-3">
          {document.data.is_placeholder ? (
            <p className="rounded border border-warn/40 bg-warn/10 p-3 text-sm text-ink">
              <b>This document has not been written yet.</b> It is placeholder text shipped with
              the application and is not an agreement.
            </p>
          ) : (
            <p className="text-xs text-faint">Version {document.data.version}</p>
          )}

          <Prose text={document.data.body} />

          <p className="pt-4 text-sm">
            <Link href="/signup" className="text-accent hover:underline">
              Back to sign up
            </Link>
          </p>
        </article>
      )}
    </AuthShell>
  );
}

/** Headings, list items and paragraphs. Everything is rendered as text. */
function Prose({ text }: { text: string }) {
  const lines = text.split("\n").filter((line) => !line.trim().startsWith("<!--"));

  return (
    <div className="space-y-2 text-sm leading-relaxed text-muted">
      {lines.map((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) return null;
        if (trimmed.startsWith("# ")) {
          return (
            <h2 key={index} className="pt-2 text-lg font-semibold text-ink">
              {trimmed.slice(2)}
            </h2>
          );
        }
        if (trimmed.startsWith("## ")) {
          return (
            <h3 key={index} className="pt-2 text-base font-semibold text-ink">
              {trimmed.slice(3)}
            </h3>
          );
        }
        if (trimmed.startsWith("- ")) {
          return (
            <p key={index} className="pl-4 before:mr-2 before:content-['•']">
              {stripEmphasis(trimmed.slice(2))}
            </p>
          );
        }
        return <p key={index}>{stripEmphasis(trimmed)}</p>;
      })}
    </div>
  );
}

/** Markdown emphasis markers would otherwise render as literal asterisks and backticks. */
function stripEmphasis(value: string): string {
  return value.replace(/\*\*(.+?)\*\*/g, "$1").replace(/`(.+?)`/g, "$1");
}
