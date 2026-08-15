"use client";

import { useMutation } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { MessageSquarePlus } from "lucide-react";

import { api } from "@/lib/api";
import { Modal } from "@/components/ui/overlay";
import { Button, Field, Select, Textarea } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";

/**
 * Tell us something, from wherever you are.
 *
 * In a beta the feedback loop is the product, and a report that has to be typed into a separate
 * tool is a report that never gets filed. The page is attached automatically because "it's broken"
 * without a URL costs an email to answer.
 *
 * Only what is genuinely diagnostic is collected — viewport and user agent. No screenshots, no
 * session recording, nothing the person did not choose to write.
 */
const KINDS = [
  { value: "bug", label: "Something is broken" },
  { value: "idea", label: "I have an idea" },
  { value: "question", label: "I have a question" },
  { value: "other", label: "Something else" },
] as const;

export function FeedbackWidget() {
  const pathname = usePathname();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<string>("bug");
  const [message, setMessage] = useState("");

  const send = useMutation({
    mutationFn: () =>
      api.post<{ id: string; message: string }>("/api/v1/feedback", {
        kind,
        message: message.trim(),
        page: pathname,
        context: {
          viewport: `${window.innerWidth}x${window.innerHeight}`,
          user_agent: navigator.userAgent,
          language: navigator.language,
        },
      }),
    onSuccess: (result) => {
      setMessage("");
      setKind("bug");
      setOpen(false);
      toast.success("Sent", result.message);
    },
    onError: (error) => toast.fromError(error, "Could not send that"),
  });

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed bottom-4 right-4 z-30 flex items-center gap-2 rounded-full border border-line bg-surface px-4 py-2.5 text-sm font-medium text-ink shadow-lg transition-colors hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <MessageSquarePlus className="h-4 w-4" aria-hidden />
        Feedback
      </button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Tell us what happened"
        description="This goes to the people building Tradeloom. We read every one."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={send.isPending}
              disabled={message.trim().length < 3}
              onClick={() => send.mutate()}
            >
              Send
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="What kind of thing is it?" htmlFor="fb-kind">
            <Select id="fb-kind" value={kind} onChange={(event) => setKind(event.target.value)}>
              {KINDS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>

          <Field
            label="What happened?"
            htmlFor="fb-message"
            hint={`Sent from ${pathname}. Your browser and window size come along too.`}
            required
          >
            <Textarea
              id="fb-message"
              rows={6}
              value={message}
              maxLength={4000}
              placeholder="The equity curve on analytics stops a day short of my last trade…"
              onChange={(event) => setMessage(event.target.value)}
            />
          </Field>
        </div>
      </Modal>
    </>
  );
}
