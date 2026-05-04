import { useEffect, useMemo, useState } from "react";
import {
  ArrowUpRight,
  BookOpen,
  CheckCircle2,
  Clock3,
  Loader2,
  MessageSquareText,
  Search,
  Sparkles,
} from "lucide-react";
import { askQuestion, listCourses } from "./lib/api";
import { compactSource, formatDate, formatNumber } from "./lib/format";
import type { Course, QueryResponse } from "./types/api";

const examples = [
  "How hard is CS 6250 if I work full time?",
  "Which OMSCS courses are lighter but still useful?",
  "Compare Computer Networks and Software Architecture and Design.",
];

type View = "ask" | "courses";

export default function App() {
  const [view, setView] = useState<View>("ask");
  const [question, setQuestion] = useState(examples[0]);
  const [query, setQuery] = useState<QueryResponse | null>(null);
  const [isAsking, setIsAsking] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [courses, setCourses] = useState<Course[]>([]);
  const [courseSearch, setCourseSearch] = useState("");
  const [coursesError, setCoursesError] = useState<string | null>(null);

  useEffect(() => {
    let ignore = false;
    listCourses()
      .then((payload) => {
        if (!ignore) {
          setCourses(payload.courses);
        }
      })
      .catch((error: Error) => {
        if (!ignore) {
          setCoursesError(error.message);
        }
      });
    return () => {
      ignore = true;
    };
  }, []);

  const filteredCourses = useMemo(() => {
    const needle = courseSearch.trim().toLowerCase();
    if (!needle) {
      return courses.slice(0, 18);
    }
    return courses
      .filter((course) => {
        const haystack = [
          course.name,
          course.slug,
          ...course.codes,
          String(course.metadata.tags ?? ""),
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(needle);
      })
      .slice(0, 24);
  }, [courseSearch, courses]);

  async function submitQuestion(nextQuestion = question) {
    const trimmed = nextQuestion.trim();
    if (!trimmed || isAsking) {
      return;
    }
    setQuestion(trimmed);
    setIsAsking(true);
    setQueryError(null);
    try {
      setQuery(await askQuestion(trimmed, 6));
    } catch (error) {
      setQueryError(error instanceof Error ? error.message : "Query failed");
    } finally {
      setIsAsking(false);
    }
  }

  return (
    <main className="min-h-screen bg-paper text-ink">
      <div className="mx-auto flex min-h-screen w-full max-w-7xl flex-col px-4 py-4 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-line pb-4 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-3">
            <div className="grid h-11 w-11 place-items-center rounded-lg bg-ink text-paper shadow-soft">
              <Sparkles className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <p className="text-sm font-semibold uppercase tracking-[0.18em] text-moss">
                OMSCS Intelligence
              </p>
              <h1 className="text-2xl font-semibold leading-tight sm:text-3xl">
                Course planning, grounded in evidence
              </h1>
            </div>
          </div>
          <nav className="flex h-11 w-full rounded-lg border border-line bg-panel p-1 md:w-auto">
            <button
              className={tabClass(view === "ask")}
              type="button"
              onClick={() => setView("ask")}
            >
              <MessageSquareText className="h-4 w-4" aria-hidden="true" />
              Ask
            </button>
            <button
              className={tabClass(view === "courses")}
              type="button"
              onClick={() => setView("courses")}
            >
              <BookOpen className="h-4 w-4" aria-hidden="true" />
              Courses
            </button>
          </nav>
        </header>

        {view === "ask" ? (
          <section className="grid flex-1 gap-6 py-6 lg:grid-cols-[minmax(0,0.95fr)_minmax(380px,0.65fr)]">
            <div className="flex min-h-[640px] flex-col rounded-lg border border-line bg-panel shadow-soft">
              <div className="border-b border-line p-4 sm:p-5">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-xl font-semibold">Ask</h2>
                    <p className="mt-1 text-sm text-ink/60">
                      Retrieved course evidence appears beside each answer.
                    </p>
                  </div>
                  {query && (
                    <div className="hidden items-center gap-2 rounded-full border border-line bg-paper px-3 py-1 text-sm text-moss sm:flex">
                      <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                      {query.chunks.length} citations
                    </div>
                  )}
                </div>
              </div>

              <div className="flex-1 p-4 sm:p-5">
                {query ? (
                  <article className="space-y-5">
                    <div className="rounded-lg bg-ink p-5 text-paper">
                      <p className="text-sm uppercase tracking-[0.18em] text-paper/60">
                        Answer
                      </p>
                      <p className="mt-3 text-lg leading-8">{query.answer}</p>
                    </div>
                    <div className="grid gap-3">
                      {query.chunks.map((chunk) => (
                        <CitationCard
                          key={`${chunk.document_id}-${chunk.chunk_index}`}
                          chunk={chunk}
                        />
                      ))}
                    </div>
                  </article>
                ) : (
                  <div className="flex h-full min-h-[420px] flex-col justify-center rounded-lg border border-dashed border-line bg-paper/60 p-6">
                    <div className="max-w-2xl">
                      <p className="text-sm font-semibold uppercase tracking-[0.18em] text-clay">
                        Ready
                      </p>
                      <h2 className="mt-3 text-3xl font-semibold leading-tight sm:text-4xl">
                        Start with a course decision.
                      </h2>
                      <div className="mt-6 flex flex-wrap gap-2">
                        {examples.map((example) => (
                          <button
                            className="rounded-full border border-line bg-panel px-4 py-2 text-left text-sm font-medium text-ink transition hover:border-ink"
                            key={example}
                            type="button"
                            onClick={() => submitQuestion(example)}
                          >
                            {example}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </div>

              <form
                className="border-t border-line p-4 sm:p-5"
                onSubmit={(event) => {
                  event.preventDefault();
                  submitQuestion();
                }}
              >
                <div className="flex flex-col gap-3 rounded-lg border border-line bg-paper p-3 focus-within:border-ink sm:flex-row">
                  <textarea
                    className="min-h-24 flex-1 resize-none bg-transparent text-base leading-7 outline-none placeholder:text-ink/35 sm:min-h-16"
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    placeholder="Ask about workload, fit, tradeoffs, or course pairings"
                  />
                  <button
                    className="inline-flex h-12 shrink-0 items-center justify-center gap-2 rounded-lg bg-ink px-5 text-sm font-semibold text-paper transition hover:bg-marine disabled:cursor-not-allowed disabled:opacity-60"
                    type="submit"
                    disabled={isAsking}
                  >
                    {isAsking ? (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <ArrowUpRight className="h-4 w-4" aria-hidden="true" />
                    )}
                    Ask
                  </button>
                </div>
                {queryError && (
                  <p className="mt-3 rounded-lg border border-clay/30 bg-clay/10 px-4 py-3 text-sm text-clay">
                    {queryError}
                  </p>
                )}
              </form>
            </div>

            <aside className="rounded-lg border border-line bg-panel p-4 shadow-soft sm:p-5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold">Course Index</h2>
                  <p className="mt-1 text-sm text-ink/60">
                    {courses.length ? `${courses.length} courses loaded` : "Loading"}
                  </p>
                </div>
                <Clock3 className="h-5 w-5 text-gold" aria-hidden="true" />
              </div>
              <CourseSearch value={courseSearch} onChange={setCourseSearch} />
              <div className="mt-4 grid max-h-[560px] gap-3 overflow-y-auto pr-1">
                {coursesError ? (
                  <p className="rounded-lg border border-clay/30 bg-clay/10 p-3 text-sm text-clay">
                    {coursesError}
                  </p>
                ) : (
                  filteredCourses.map((course) => (
                    <CourseRow key={course.slug} course={course} />
                  ))
                )}
              </div>
            </aside>
          </section>
        ) : (
          <section className="flex-1 py-6">
            <div className="rounded-lg border border-line bg-panel p-4 shadow-soft sm:p-5">
              <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
                <div>
                  <h2 className="text-2xl font-semibold">Courses</h2>
                  <p className="mt-1 text-sm text-ink/60">
                    {courses.length} courses from the local catalog
                  </p>
                </div>
                <div className="w-full md:w-96">
                  <CourseSearch value={courseSearch} onChange={setCourseSearch} />
                </div>
              </div>
              <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {filteredCourses.map((course) => (
                  <CourseCard key={course.slug} course={course} />
                ))}
              </div>
            </div>
          </section>
        )}
      </div>
    </main>
  );
}

function tabClass(active: boolean) {
  return [
    "inline-flex flex-1 items-center justify-center gap-2 rounded-md px-4 text-sm font-semibold transition md:flex-none",
    active ? "bg-ink text-paper shadow-sm" : "text-ink/65 hover:bg-paper",
  ].join(" ");
}

function CourseSearch({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="mt-4 flex h-11 items-center gap-2 rounded-lg border border-line bg-paper px-3 text-sm focus-within:border-ink">
      <Search className="h-4 w-4 text-ink/45" aria-hidden="true" />
      <input
        className="w-full bg-transparent outline-none placeholder:text-ink/35"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="Search code, title, tag"
      />
    </label>
  );
}

function CourseRow({ course }: { course: Course }) {
  return (
    <div className="rounded-lg border border-line bg-paper p-3 transition hover:border-ink">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-marine">
            {course.codes.join(", ") || "Course"}
          </p>
          <h3 className="mt-1 text-sm font-semibold leading-5">{course.name}</h3>
        </div>
        <Metric value={course.workload} suffix="h" />
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <MiniStat label="Rating" value={formatNumber(course.rating)} />
        <MiniStat label="Diff" value={formatNumber(course.difficulty)} />
        <MiniStat label="Reviews" value={String(course.review_count)} />
      </div>
    </div>
  );
}

function CourseCard({ course }: { course: Course }) {
  const tags = Array.isArray(course.metadata.tags)
    ? course.metadata.tags.slice(0, 3).map(String)
    : [];

  return (
    <article className="flex min-h-64 flex-col rounded-lg border border-line bg-paper p-4 transition hover:border-ink">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-marine">
            {course.codes.join(", ")}
          </p>
          <h3 className="mt-2 text-lg font-semibold leading-6">{course.name}</h3>
        </div>
        <Metric value={course.workload} suffix="h" />
      </div>
      <p className="mt-3 line-clamp-3 text-sm leading-6 text-ink/65">
        {course.description || "No description available."}
      </p>
      <div className="mt-auto pt-4">
        <div className="grid grid-cols-3 gap-2 text-xs">
          <MiniStat label="Rating" value={formatNumber(course.rating)} />
          <MiniStat label="Difficulty" value={formatNumber(course.difficulty)} />
          <MiniStat label="Reviews" value={String(course.review_count)} />
        </div>
        {tags.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {tags.map((tag) => (
              <span
                className="rounded-full border border-line bg-panel px-2.5 py-1 text-xs font-medium text-ink/70"
                key={tag}
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

function CitationCard({
  chunk,
}: {
  chunk: QueryResponse["chunks"][number];
}) {
  return (
    <article className="rounded-lg border border-line bg-paper p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-marine">
            <span>{compactSource(chunk.source)}</span>
            <span className="h-1 w-1 rounded-full bg-line" />
            <span>{formatDate(chunk.published_at)}</span>
          </div>
          <h3 className="mt-2 text-base font-semibold leading-6">
            {chunk.title || chunk.course_name || "Retrieved evidence"}
          </h3>
          {chunk.course_codes.length > 0 && (
            <p className="mt-1 text-sm text-ink/55">
              {chunk.course_name} · {chunk.course_codes.join(", ")}
            </p>
          )}
        </div>
        {chunk.url && (
          <a
            className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-lg border border-line bg-panel px-3 text-sm font-semibold text-ink transition hover:border-ink"
            href={chunk.url}
            rel="noreferrer"
            target="_blank"
          >
            <ArrowUpRight className="h-4 w-4" aria-hidden="true" />
            Open
          </a>
        )}
      </div>
      <p className="mt-4 border-l-2 border-gold pl-4 text-sm leading-7 text-ink/72">
        {chunk.text}
      </p>
    </article>
  );
}

function Metric({ value, suffix }: { value: number | null; suffix: string }) {
  return (
    <div className="grid h-14 w-14 shrink-0 place-items-center rounded-lg border border-line bg-panel text-center">
      <span className="text-sm font-bold">
        {value === null ? "—" : `${formatNumber(value, 0)}${suffix}`}
      </span>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-panel px-2 py-2">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink/45">
        {label}
      </p>
      <p className="mt-1 font-semibold text-ink">{value}</p>
    </div>
  );
}
