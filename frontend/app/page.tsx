"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import gsap from "gsap";

type SourceGroup = {
  id: string;
  label: string;
  description: string;
  domains: string[];
};

type EvidenceItem = {
  id: string;
  title: string;
  url: string;
  sourceGroup: string;
  snippet: string;
};

type AskResponse = {
  ok: boolean;
  answer?: string;
  evidence?: EvidenceItem[];
  needsHumanReview?: boolean;
  route?: Array<{ id: string; label: string; domains: string[] }>;
  error?: string;
};

type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  sources?: EvidenceItem[];
  state?: "loading" | "final";
  kind?: "answer" | "system";
};

const seedQuestions = [
  "What are the first-line guideline-based treatments for uncomplicated cystitis in adults?",
  "Is amoxicillin recommended for uncomplicated cystitis?",
  "What trials support metformin for diabetes?",
];

export default function Page() {
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      role: "assistant",
      text: "Welcome. I am your Clinical Evidence Assistant. I provide evidence-based analysis from trusted sources like NICE and MedlinePlus.",
      sources: [],
      state: "final",
      kind: "system",
    },
  ]);
  const [groups, setGroups] = useState<SourceGroup[]>([]);
  const [status, setStatus] = useState("Agent Ready");
  const [loading, setLoading] = useState(false);
  
  const shellRef = useRef<HTMLDivElement>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);

  // Initial Entry Animations
  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from(".sidebar", {
        x: -50,
        opacity: 0,
        duration: 1,
        ease: "power3.out",
      });
      gsap.from(".topbar", {
        y: -20,
        opacity: 0,
        duration: 0.8,
        delay: 0.2,
        ease: "power2.out",
      });
      gsap.from(".info-panel", {
        x: 30,
        opacity: 0,
        duration: 1,
        delay: 0.4,
        ease: "power3.out",
      });
      gsap.from(".composer", {
        y: 30,
        opacity: 0,
        duration: 0.8,
        delay: 0.6,
        ease: "power2.out",
      });
    }, shellRef);

    return () => ctx.revert();
  }, []);

  useEffect(() => {
    void fetch(`/api/sources`)
      .then((res) => res.json())
      .then((json: { groups?: SourceGroup[] }) => setGroups(json.groups ?? []))
      .catch(() => setStatus("Offline"));
  }, []);

  // Scroll and Animate New Messages
  useEffect(() => {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTo({
        top: transcriptRef.current.scrollHeight,
        behavior: "smooth",
      });

      // Animate last message
      const lastMessage = transcriptRef.current.lastElementChild;
      if (lastMessage) {
        gsap.from(lastMessage, {
          y: 20,
          opacity: 0,
          duration: 0.5,
          ease: "back.out(1.2)",
        });
      }
    }
  }, [messages]);

  const recentQueries = useMemo(() => {
    return messages
      .filter((message) => message.role === "user")
      .slice(-5)
      .map((message) => message.text);
  }, [messages]);

  async function sendQuery(text: string) {
    const value = text.trim();
    if (!value || loading) return;

    setLoading(true);
    setQuery("");
    setStatus("Searching...");

    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: "user",
      text: value,
      state: "final",
    };
    const assistantId = crypto.randomUUID();

    setMessages((current) => [
      ...current,
      userMessage,
      {
        id: assistantId,
        role: "assistant",
        text: "Analyzing clinical evidence...",
        sources: [],
        state: "loading",
        kind: "system",
      },
    ]);

    try {
      const response = await fetch(`/api/ask`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ query: value }),
      });
      const data: AskResponse = await response.json();

      const answerText = data.answer || "No verified evidence found.";

      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                text: answerText,
                sources: data.evidence ?? [],
                state: "final",
                kind: data.needsHumanReview ? "system" : "answer",
              }
            : message,
        ),
      );
      setStatus("Agent Ready");
    } catch {
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                text: "Failed to fetch evidence.",
                sources: [],
                state: "final",
                kind: "system",
              }
            : message,
        ),
      );
      setStatus("Error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="shell" ref={shellRef}>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">C</div>
          <div>
            <div className="eyebrow">ClinicalAgent</div>
            <h1>Evidence Search</h1>
          </div>
        </div>

        <div className="sidebar-section">
          <p className="section-label">General</p>
          <button
            className="sidebar-item active"
            type="button"
            onClick={() => {
              setMessages([messages[0]]);
              setQuery("");
            }}
          >
            New Analysis
          </button>
          <button className="sidebar-item" type="button" onClick={() => sendQuery(seedQuestions[1])}>
            Guidelines
          </button>
        </div>

        <div className="sidebar-section">
          <p className="section-label">Recent STORY</p>
          <div className="history-list">
            {recentQueries.length ? (
              recentQueries.map((item, i) => (
                <button key={i} className="history-item" type="button" onClick={() => sendQuery(item)}>
                  {item}
                </button>
              ))
            ) : (
              <div className="history-empty">Previous queries will appear here.</div>
            )}
          </div>
        </div>

        <div className="user-card">
          <div className="avatar">DR</div>
          <div>
            <div className="user-name">Dr. Clinical</div>
            <div className="user-role">Lead Physician</div>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <p className="eyebrow">Clinical Intelligence</p>
            <h2>Verified Evidence Analysis</h2>
          </div>
          <div className="status-pill">{status}</div>
        </header>

        <div className="content">
          <section className="transcript" ref={transcriptRef}>
            {messages.map((message) => (
              <article key={message.id} className={`message ${message.role}`}>
                <div className="bubble">
                  {message.kind === "system" ? (
                    <div className="system-copy">{message.text}</div>
                  ) : (
                    <p>{message.text}</p>
                  )}
                  {message.state === "loading" ? (
                    <div className="loading-row">
                      <span />
                      <span />
                      <span />
                    </div>
                  ) : null}
                  {message.sources && message.sources.length > 0 ? (
                    <div className="source-block">
                      <div className="source-title">CLINICAL REFERENCES</div>
                      <div className="source-links">
                        {message.sources.map((source) => (
                          <a
                            key={source.id}
                            className="source-chip"
                            href={source.url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <strong>REF</strong>
                            <span>{source.title}</span>
                          </a>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              </article>
            ))}
          </section>

          <aside className="info-panel">
            <div className="panel-card">
              <div className="eyebrow">Verified Sources</div>
              <div className="group-list">
                {groups.map((group) => (
                  <div key={group.id} className="group-card">
                    <strong>{group.label}</strong>
                    <p>{group.description}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="panel-card">
              <div className="eyebrow">Quick Inquiries</div>
              <div className="example-list">
                {seedQuestions.map((item) => (
                  <button key={item} type="button" className="example-chip" onClick={() => sendQuery(item)}>
                    {item}
                  </button>
                ))}
              </div>
            </div>
          </aside>
        </div>

        <div className="composer">
          <form
            className="composer-container"
            onSubmit={(event) => {
              event.preventDefault();
              void sendQuery(query);
            }}
          >
            <textarea
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Enter clinical query (e.g. Treatment for AF)..."
              rows={1}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void sendQuery(query);
                }
              }}
            />
            <div className="composer-actions">
              <button
                type="button"
                className="secondary"
                onClick={() => sendQuery(seedQuestions[0])}
              >
                Use Example
              </button>
              <button type="submit" disabled={loading || !query.trim()}>
                {loading ? "Analyzing..." : "Analyze"}
              </button>
            </div>
          </form>
        </div>
      </main>
    </div>
  );
}
