import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowUpRight,
  BookOpen,
  Database,
  GitCompareArrows,
  Layers3,
  Loader2,
  MessageSquareText,
  Search,
  SlidersHorizontal,
  Sparkles,
  X,
} from "lucide-react";
import { askQuestion, getCourse, listCourseDocuments, listCourses } from "./lib/api";
import { compactSource, formatDate, formatNumber } from "./lib/format";
import type { Course, CourseDocument, QueryResponse } from "./types/api";

const examples = [
  "What should I know before taking Graduate Algorithms?",
  "Compare Machine Learning, AI, and Deep Learning for workload and payoff.",
  "Which foundational course is safest while working full time?",
];

type View = "ask" | "courses";
type FilterBand = "all" | "light" | "balanced" | "heavy";
type CitationSort = "match" | "newest" | "oldest";

const priorityCourseSlugs = [
  "graduate-algorithms",
  "machine-learning",
  "artificial-intelligence",
  "computer-networks",
  "software-development-process",
  "introduction-to-operating-systems",
  "advanced-operating-systems",
  "database-systems-concepts-and-design",
  "human-computer-interaction",
  "machine-learning-for-trading",
  "deep-learning",
  "reinforcement-learning",
  "natural-language-processing",
];

const priorityCourseCodes = new Set([
  "GA",
  "ML",
  "AI",
  "CN",
  "SDP",
  "GIOS",
  "AOS",
  "DBS",
  "HCI",
  "ML4T",
  "DL",
  "RL",
  "NLP",
]);

export default function App() {
  const [view, setView] = useState<View>("ask");
  const [question, setQuestion] = useState(examples[0]);
  const [query, setQuery] = useState<QueryResponse | null>(null);
  const [isAsking, setIsAsking] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [courses, setCourses] = useState<Course[]>([]);
  const [courseSearch, setCourseSearch] = useState("");
  const [coursesError, setCoursesError] = useState<string | null>(null);
  const [workloadFilter, setWorkloadFilter] = useState<FilterBand>("all");
  const [difficultyFilter, setDifficultyFilter] = useState<FilterBand>("all");
  const [selectedCourse, setSelectedCourse] = useState<Course | null>(null);
  const [selectedDocuments, setSelectedDocuments] = useState<CourseDocument[]>([]);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [compareCourses, setCompareCourses] = useState<Course[]>([]);
  const [citationSort, setCitationSort] = useState<CitationSort>("match");
  const answerRef = useRef<HTMLElement | null>(null);

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
    return courses
      .filter((course) => {
        if (!matchesBand(course.workload, workloadFilter, "workload")) {
          return false;
        }
        if (!matchesBand(course.difficulty, difficultyFilter, "difficulty")) {
          return false;
        }
        if (!needle) {
          return true;
        }
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
      .slice(0, view === "ask" ? 18 : 60);
  }, [courseSearch, courses, difficultyFilter, view, workloadFilter]);

  const priorityCourses = useMemo(() => {
    return courses
      .filter(
        (course) =>
          priorityCourseSlugs.includes(course.slug) ||
          course.codes.some((code) => priorityCourseCodes.has(code.toUpperCase())),
      )
      .sort((first, second) => {
        const firstIndex = priorityCourseSlugs.indexOf(first.slug);
        const secondIndex = priorityCourseSlugs.indexOf(second.slug);
        return normalizePriorityIndex(firstIndex) - normalizePriorityIndex(secondIndex);
      });
  }, [courses]);

  const sortedChunks = useMemo(() => {
    if (!query) {
      return [];
    }
    return [...query.chunks].sort((first, second) =>
      compareChunks(first, second, citationSort),
    );
  }, [citationSort, query]);

  useEffect(() => {
    if (!query) {
      return;
    }
    window.requestAnimationFrame(() => {
      answerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, [query]);

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

  async function openCourse(course: Course) {
    setView("courses");
    setSelectedCourse(course);
    setSelectedDocuments([]);
    setDetailError(null);
    setIsLoadingDetail(true);
    try {
      const [detail, documents] = await Promise.all([
        getCourse(course.slug),
        listCourseDocuments(course.slug),
      ]);
      setSelectedCourse(detail);
      setSelectedDocuments(documents.documents);
    } catch (error) {
      setDetailError(error instanceof Error ? error.message : "Course load failed");
    } finally {
      setIsLoadingDetail(false);
    }
  }

  function toggleCompare(course: Course) {
    setCompareCourses((current) => {
      if (current.some((item) => item.slug === course.slug)) {
        return current.filter((item) => item.slug !== course.slug);
      }
      if (current.length >= 4) {
        return current;
      }
      return [...current, course];
    });
  }

  function askAboutCourse(course: Course) {
    const code = course.codes[0] || course.name;
    const prompt = `What should I know before taking ${code} (${course.name})? Cover workload, difficulty, fit for full-time workers, and common tradeoffs.`;
    setQuestion(prompt);
    setView("ask");
    submitQuestion(prompt);
  }

  function compareSelectedCourses() {
    if (compareCourses.length < 2) {
      return;
    }
    const names = compareCourses
      .map((course) => `${course.codes[0] || course.name} (${course.name})`)
      .join(" vs ");
    const prompt = `Compare ${names} on workload, difficulty, project/exam heaviness, usefulness, and suitability for someone working full time. Use cited course evidence.`;
    setQuestion(prompt);
    setView("ask");
    submitQuestion(prompt);
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
                OMSCS Lens
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
          <section className="flex flex-1 justify-center py-8 sm:py-12">
            <div className="w-full max-w-4xl">
              <div className="mx-auto max-w-3xl text-center">
                <p className="text-sm font-semibold uppercase tracking-[0.18em] text-moss">
                  Ask
                </p>
                <h2 className="mt-4 text-3xl font-semibold leading-tight sm:text-5xl">
                  Make course decisions less fuzzy.
                </h2>
                <p className="mx-auto mt-4 max-w-2xl text-base leading-7 text-ink/62">
                  Ask about workload, difficulty, sequencing, tradeoffs, or fit.
                  Answers use OMSCentral reviews and curated Reddit evidence.
                </p>
              </div>

              <form
                className="mx-auto mt-8 max-w-3xl"
                onSubmit={(event) => {
                  event.preventDefault();
                  submitQuestion();
                }}
              >
                <div className="rounded-lg border border-line bg-panel p-3 shadow-soft transition focus-within:border-ink">
                  <textarea
                    className="min-h-28 w-full resize-none bg-transparent px-2 py-2 text-base leading-7 outline-none placeholder:text-ink/35 sm:min-h-24"
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    placeholder="Ask about workload, fit, tradeoffs, or course pairings"
                  />
                  <div className="flex flex-col gap-3 border-t border-line pt-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex flex-wrap gap-2">
                      {examples.slice(0, 2).map((example) => (
                        <button
                          className="rounded-full border border-line bg-paper px-3 py-1.5 text-left text-xs font-medium text-ink/70 transition hover:border-ink hover:text-ink"
                          key={example}
                          type="button"
                          onClick={() => submitQuestion(example)}
                        >
                          {example}
                        </button>
                      ))}
                    </div>
                    <button
                      className="inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-lg bg-ink px-5 text-sm font-semibold text-paper transition hover:bg-marine disabled:cursor-not-allowed disabled:opacity-60"
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
                </div>
                {queryError && (
                  <p className="mt-3 rounded-lg border border-clay/30 bg-clay/10 px-4 py-3 text-sm text-clay">
                    {queryError}
                  </p>
                )}
              </form>

              <FocusCourseStrip
                courses={priorityCourses}
                onAsk={askAboutCourse}
                onBrowse={() => setView("courses")}
              />

              {isAsking && (
                <div className="mx-auto mt-10 flex max-w-3xl items-center gap-3 border-t border-line pt-8 text-sm font-medium text-ink/60">
                  <span className="relative flex h-5 w-5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-marine/25" />
                    <span className="relative inline-flex h-5 w-5 rounded-full border-2 border-marine border-t-transparent animate-spin" />
                  </span>
                  Gathering course evidence
                </div>
              )}

              {query && (
                <article
                  className="answer-enter mx-auto mt-12 max-w-3xl border-t border-line pt-8"
                  ref={answerRef}
                >
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <p className="text-sm font-semibold uppercase tracking-[0.18em] text-clay">
                        Answer
                      </p>
                      <p className="mt-1 text-sm text-ink/55">
                        {sortedChunks.length} citations retrieved
                      </p>
                    </div>
                  </div>

                  <MarkdownAnswer text={query.answer} />

                  <section className="mt-10">
                    <div className="flex flex-col gap-3 border-t border-line pt-6 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <h3 className="text-lg font-semibold">Evidence</h3>
                        <p className="mt-1 text-sm text-ink/55">
                          Source chunks used by retrieval for this answer.
                        </p>
                      </div>
                      <div className="flex items-center gap-3">
                        <CitationSortControl
                          value={citationSort}
                          onChange={setCitationSort}
                        />
                        <Database className="hidden h-5 w-5 text-marine sm:block" aria-hidden="true" />
                      </div>
                    </div>
                    <div className="mt-4 grid gap-3">
                      {sortedChunks.map((chunk) => (
                        <CitationCard
                          key={`${chunk.document_id}-${chunk.chunk_index}`}
                          chunk={chunk}
                        />
                      ))}
                    </div>
                  </section>
                </article>
              )}
            </div>
          </section>
        ) : (
          <section className="flex-1 py-6">
            <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
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
              <div className="mt-4 grid gap-3 lg:grid-cols-2">
                <FilterGroup
                  label="Workload"
                  value={workloadFilter}
                  onChange={setWorkloadFilter}
                />
                <FilterGroup
                  label="Difficulty"
                  value={difficultyFilter}
                  onChange={setDifficultyFilter}
                />
              </div>
              <CompareBar
                courses={compareCourses}
                onRemove={(slug) =>
                  setCompareCourses((current) =>
                    current.filter((course) => course.slug !== slug),
                  )
                }
                onCompare={compareSelectedCourses}
              />
              <div className="mt-5 grid gap-3 md:grid-cols-2">
                {filteredCourses.map((course) => (
                  <CourseCard
                    key={course.slug}
                    course={course}
                    selected={selectedCourse?.slug === course.slug}
                    compareSelected={compareCourses.some(
                      (item) => item.slug === course.slug,
                    )}
                    onOpen={openCourse}
                    onToggleCompare={toggleCompare}
                  />
                ))}
              </div>
            </div>
            <CourseDetailPanel
              course={selectedCourse}
              documents={selectedDocuments}
              isLoading={isLoadingDetail}
              error={detailError}
              onAsk={askAboutCourse}
            />
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

function matchesBand(
  value: number | null,
  band: FilterBand,
  kind: "workload" | "difficulty",
) {
  if (band === "all" || value === null) {
    return true;
  }
  if (kind === "workload") {
    if (band === "light") {
      return value <= 10;
    }
    if (band === "balanced") {
      return value > 10 && value <= 16;
    }
    return value > 16;
  }
  if (band === "light") {
    return value <= 2.5;
  }
  if (band === "balanced") {
    return value > 2.5 && value <= 3.7;
  }
  return value > 3.7;
}

function FilterGroup({
  label,
  value,
  onChange,
}: {
  label: string;
  value: FilterBand;
  onChange: (value: FilterBand) => void;
}) {
  const options: Array<{ label: string; value: FilterBand }> = [
    { label: "All", value: "all" },
    { label: "Light", value: "light" },
    { label: "Balanced", value: "balanced" },
    { label: "Heavy", value: "heavy" },
  ];

  return (
    <div className="rounded-lg border border-line bg-paper p-2">
      <div className="mb-2 flex items-center gap-2 px-1 text-xs font-semibold uppercase tracking-[0.14em] text-ink/45">
        <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
        {label}
      </div>
      <div className="grid grid-cols-4 gap-1">
        {options.map((option) => (
          <button
            className={[
              "h-9 rounded-md text-xs font-semibold transition",
              option.value === value
                ? "bg-ink text-paper"
                : "text-ink/60 hover:bg-panel",
            ].join(" ")}
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function CompareBar({
  courses,
  onRemove,
  onCompare,
}: {
  courses: Course[];
  onRemove: (slug: string) => void;
  onCompare: () => void;
}) {
  if (courses.length === 0) {
    return null;
  }

  return (
    <div className="mt-4 flex flex-col gap-3 rounded-lg border border-line bg-ink p-3 text-paper lg:flex-row lg:items-center lg:justify-between">
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-2 text-sm font-semibold">
          <GitCompareArrows className="h-4 w-4" aria-hidden="true" />
          Compare
        </span>
        {courses.map((course) => (
          <span
            className="inline-flex items-center gap-2 rounded-full bg-paper/12 px-3 py-1 text-sm"
            key={course.slug}
          >
            {course.codes[0] || course.name}
            <button
              className="grid h-5 w-5 place-items-center rounded-full hover:bg-paper/15"
              type="button"
              onClick={() => onRemove(course.slug)}
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </span>
        ))}
      </div>
      <button
        className="inline-flex h-10 items-center justify-center rounded-lg bg-paper px-4 text-sm font-semibold text-ink disabled:cursor-not-allowed disabled:opacity-50"
        type="button"
        disabled={courses.length < 2}
        onClick={onCompare}
      >
        Compare selected
      </button>
    </div>
  );
}

function FocusCourseStrip({
  courses,
  onAsk,
  onBrowse,
}: {
  courses: Course[];
  onAsk: (course: Course) => void;
  onBrowse: () => void;
}) {
  if (courses.length === 0) {
    return null;
  }

  return (
    <div className="mx-auto mt-6 max-w-3xl border-t border-line pt-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold text-ink/62">
          <Layers3 className="h-4 w-4 text-marine" aria-hidden="true" />
          Focus courses
        </div>
        <button
          className="text-left text-sm font-semibold text-marine transition hover:text-ink sm:text-right"
          type="button"
          onClick={onBrowse}
        >
          Browse catalog
        </button>
      </div>
      <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
        {courses.map((course) => (
          <button
            className="h-9 shrink-0 rounded-full border border-line bg-transparent px-3 text-sm font-semibold text-ink/70 transition hover:border-ink hover:bg-panel hover:text-ink"
            key={course.slug}
            type="button"
            onClick={() => onAsk(course)}
            title={`Ask about ${course.name}`}
          >
            {course.codes[0] || course.name}
          </button>
        ))}
      </div>
    </div>
  );
}

function CitationSortControl({
  value,
  onChange,
}: {
  value: CitationSort;
  onChange: (value: CitationSort) => void;
}) {
  const options: Array<{ label: string; value: CitationSort }> = [
    { label: "Best match", value: "match" },
    { label: "Newest", value: "newest" },
    { label: "Oldest", value: "oldest" },
  ];

  return (
    <div className="grid h-10 grid-cols-3 rounded-lg border border-line bg-panel p-1 sm:w-80">
      {options.map((option) => (
        <button
          className={[
            "rounded-md px-2 text-xs font-semibold transition",
            option.value === value
              ? "bg-ink text-paper"
              : "text-ink/60 hover:bg-paper",
          ].join(" ")}
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
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

function CourseRow({
  course,
  onOpen,
}: {
  course: Course;
  onOpen: (course: Course) => void;
}) {
  return (
    <button
      className="rounded-lg border border-line bg-paper p-3 text-left transition hover:border-ink"
      type="button"
      onClick={() => onOpen(course)}
    >
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
    </button>
  );
}

function CourseCard({
  course,
  selected,
  compareSelected,
  onOpen,
  onToggleCompare,
}: {
  course: Course;
  selected: boolean;
  compareSelected: boolean;
  onOpen: (course: Course) => void;
  onToggleCompare: (course: Course) => void;
}) {
  const tags = Array.isArray(course.metadata.tags)
    ? course.metadata.tags.slice(0, 3).map(String)
    : [];

  return (
    <article
      className={[
        "flex min-h-72 flex-col rounded-lg border bg-paper p-4 transition",
        selected ? "border-ink shadow-soft" : "border-line hover:border-ink",
      ].join(" ")}
    >
      <div className="flex items-start justify-between gap-3">
        <button
          className="min-w-0 flex-1 text-left"
          type="button"
          onClick={() => onOpen(course)}
        >
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-marine">
            {course.codes.join(", ")}
          </p>
          <h3 className="mt-2 text-lg font-semibold leading-6">{course.name}</h3>
        </button>
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
        <div className="mt-4 flex gap-2">
          <button
            className="inline-flex h-10 flex-1 items-center justify-center rounded-lg bg-ink px-3 text-sm font-semibold text-paper transition hover:bg-marine"
            type="button"
            onClick={() => onOpen(course)}
          >
            Details
          </button>
          <button
            className={[
              "inline-flex h-10 flex-1 items-center justify-center rounded-lg border px-3 text-sm font-semibold transition",
              compareSelected
                ? "border-ink bg-panel text-ink"
                : "border-line bg-panel text-ink/70 hover:border-ink",
            ].join(" ")}
            type="button"
            onClick={() => onToggleCompare(course)}
          >
            {compareSelected ? "Selected" : "Compare"}
          </button>
        </div>
      </div>
    </article>
  );
}

function CourseDetailPanel({
  course,
  documents,
  isLoading,
  error,
  onAsk,
}: {
  course: Course | null;
  documents: CourseDocument[];
  isLoading: boolean;
  error: string | null;
  onAsk: (course: Course) => void;
}) {
  const sourceStats = getSourceStats(documents);

  if (!course) {
    return (
      <aside className="rounded-lg border border-dashed border-line bg-panel p-5 shadow-soft">
        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-clay">
          Detail
        </p>
        <h2 className="mt-3 text-2xl font-semibold">Pick a course</h2>
        <p className="mt-2 text-sm leading-6 text-ink/60">
          Select a course to inspect its workload, difficulty, source material,
          and evidence coverage.
        </p>
      </aside>
    );
  }

  return (
    <aside className="rounded-lg border border-line bg-panel p-5 shadow-soft xl:sticky xl:top-5 xl:self-start">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-marine">
            {course.codes.join(", ") || "Course"}
          </p>
          <h2 className="mt-2 text-2xl font-semibold leading-tight">{course.name}</h2>
        </div>
        <Metric value={course.workload} suffix="h" />
      </div>

      <p className="mt-4 text-sm leading-6 text-ink/65">
        {course.description || "No course description is available yet."}
      </p>

      <div className="mt-4 grid grid-cols-3 gap-2 text-xs">
        <MiniStat label="Rating" value={formatNumber(course.rating)} />
        <MiniStat label="Difficulty" value={formatNumber(course.difficulty)} />
        <MiniStat label="Reviews" value={String(course.review_count)} />
      </div>

      <button
        className="mt-4 inline-flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-ink px-4 text-sm font-semibold text-paper transition hover:bg-marine"
        type="button"
        onClick={() => onAsk(course)}
      >
        <MessageSquareText className="h-4 w-4" aria-hidden="true" />
        Ask about this course
      </button>

      <div className="mt-5 border-t border-line pt-5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold">Sources</h3>
            <p className="mt-1 text-sm text-ink/55">
              {isLoading ? "Loading evidence" : `${documents.length} documents`}
            </p>
          </div>
          <BookOpen className="h-5 w-5 text-gold" aria-hidden="true" />
        </div>

        {!isLoading && documents.length > 0 && (
          <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
            <MiniStat label="OMSC" value={String(sourceStats.omscentral)} />
            <MiniStat label="Reddit" value={String(sourceStats.reddit)} />
            <MiniStat label="Chunks" value={String(sourceStats.chunks)} />
          </div>
        )}

        {error && (
          <p className="mt-3 rounded-lg border border-clay/30 bg-clay/10 px-3 py-2 text-sm text-clay">
            {error}
          </p>
        )}

        <div className="mt-3 grid max-h-[420px] gap-3 overflow-y-auto pr-1">
          {isLoading ? (
            <div className="flex items-center gap-2 rounded-lg border border-line bg-paper p-3 text-sm text-ink/60">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Loading source documents
            </div>
          ) : documents.length === 0 ? (
            <div className="rounded-lg border border-dashed border-line bg-paper p-4 text-sm leading-6 text-ink/60">
              No source documents are attached to this course yet.
            </div>
          ) : (
            documents.slice(0, 12).map((document) => (
              <SourceDocumentCard key={document.document_id} document={document} />
            ))
          )}
        </div>
      </div>
    </aside>
  );
}

function SourceDocumentCard({ document }: { document: CourseDocument }) {
  return (
    <article className="rounded-lg border border-line bg-paper p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <SourceBadge source={document.source} />
            <span className="text-xs font-semibold uppercase tracking-[0.14em] text-ink/45">
              {formatDate(document.published_at)}
            </span>
          </div>
          <h4 className="mt-2 text-sm font-semibold leading-5">{document.title}</h4>
        </div>
        <span className="rounded-full bg-panel px-2 py-1 text-xs font-semibold text-ink/60">
          {document.chunk_count} chunks
        </span>
      </div>
      <a
        className="mt-3 inline-flex items-center gap-1 text-sm font-semibold text-marine hover:text-ink"
        href={document.url}
        rel="noreferrer"
        target="_blank"
      >
        Open source
        <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
      </a>
    </article>
  );
}

function CitationCard({
  chunk,
}: {
  chunk: QueryResponse["chunks"][number];
}) {
  const title = formatCitationTitle(chunk);
  const evidence = formatEvidenceText(chunk.text);

  return (
    <article className="overflow-hidden rounded-lg border border-line bg-paper p-4">
      <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <SourceBadge source={chunk.source} />
            <span className="h-1 w-1 rounded-full bg-line" />
            <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink/45">
              {formatDate(chunk.published_at)}
            </span>
            <span className="h-1 w-1 rounded-full bg-line" />
            <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink/45">
              Match {formatNumber(chunk.score, 2)}
            </span>
          </div>
          <h3 className="mt-2 break-words text-sm font-semibold leading-5 sm:text-base sm:leading-6">
            {title}
          </h3>
          {chunk.course_codes.length > 0 && (
            <p className="mt-1 break-words text-xs leading-5 text-ink/55 sm:text-sm">
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
      <div className="mt-4 border-l-2 border-gold pl-4">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink/40">
          Evidence excerpt
        </p>
        <p className="mt-2 max-h-36 overflow-y-auto whitespace-pre-wrap break-words pr-2 text-sm leading-6 text-ink/72">
          {evidence}
        </p>
      </div>
    </article>
  );
}

function formatCitationTitle(chunk: QueryResponse["chunks"][number]) {
  if (chunk.title) {
    return chunk.title.replace(/\s+review by .+$/i, " review");
  }
  if (chunk.course_name) {
    return `${chunk.course_name} evidence`;
  }
  return "Retrieved evidence";
}

function formatEvidenceText(text: string) {
  return text.replace(/^Course:[\s\S]*?\n\n/, "").trim();
}

function MarkdownAnswer({ text }: { text: string }) {
  const blocks = parseMarkdownBlocks(text);

  return (
    <div className="mt-6 space-y-5 text-base leading-8 text-ink/82 sm:text-lg sm:leading-9">
      {blocks.map((block, index) => {
        if (block.type === "heading") {
          return (
            <h3
              className="text-xl font-semibold leading-7 text-ink"
              key={`${block.type}-${index}`}
            >
              {renderInlineMarkdown(block.lines[0])}
            </h3>
          );
        }

        if (block.type === "ordered-list") {
          return (
            <ol
              className="list-decimal space-y-2 pl-5 marker:text-ink/45"
              key={`${block.type}-${index}`}
            >
              {block.lines.map((line, lineIndex) => (
                <li key={`${index}-${lineIndex}`}>
                  {renderInlineMarkdown(line.replace(/^\d+\.\s+/, ""))}
                </li>
              ))}
            </ol>
          );
        }

        if (block.type === "unordered-list") {
          return (
            <ul
              className="list-disc space-y-2 pl-5 marker:text-ink/45"
              key={`${block.type}-${index}`}
            >
              {block.lines.map((line, lineIndex) => (
                <li key={`${index}-${lineIndex}`}>
                  {renderInlineMarkdown(line.replace(/^[-*]\s+/, ""))}
                </li>
              ))}
            </ul>
          );
        }

        if (block.type === "table") {
          return (
            <MarkdownTable
              block={block}
              key={`${block.type}-${index}`}
            />
          );
        }

        if (block.type === "quote") {
          return (
            <blockquote
              className="border-l-2 border-gold pl-4 text-ink/68"
              key={`${block.type}-${index}`}
            >
              {renderInlineMarkdown(block.lines.join(" ").replace(/^>\s?/, ""))}
            </blockquote>
          );
        }

        return (
          <p key={`${block.type}-${index}`}>
            {renderInlineMarkdown(block.lines.join(" "))}
          </p>
        );
      })}
    </div>
  );
}

type MarkdownBlock = {
  type:
    | "heading"
    | "ordered-list"
    | "unordered-list"
    | "paragraph"
    | "quote"
    | "table";
  lines: string[];
};

function parseMarkdownBlocks(text: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let pending: MarkdownBlock | null = null;

  function flush() {
    if (pending && pending.lines.length > 0) {
      blocks.push(pending);
    }
    pending = null;
  }

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flush();
      continue;
    }

    const heading = line.match(/^#{1,3}\s+(.+)$/);
    if (heading) {
      flush();
      blocks.push({ type: "heading", lines: [heading[1]] });
      continue;
    }

    const type = getMarkdownLineType(line);
    if (type === "table-separator") {
      continue;
    }
    if (!pending || pending.type !== type || type === "heading") {
      flush();
      pending = { type, lines: [] };
    }
    pending.lines.push(line);
  }

  flush();
  return blocks;
}

function getMarkdownLineType(line: string): MarkdownBlock["type"] | "table-separator" {
  if (/^\d+\.\s+/.test(line)) {
    return "ordered-list";
  }
  if (/^[-*]\s+/.test(line)) {
    return "unordered-list";
  }
  if (/^>\s?/.test(line)) {
    return "quote";
  }
  if (isMarkdownTableSeparator(line)) {
    return "table-separator";
  }
  if (isMarkdownTableRow(line)) {
    return "table";
  }
  return "paragraph";
}

function isMarkdownTableRow(line: string) {
  return line.startsWith("|") && line.endsWith("|") && line.split("|").length >= 4;
}

function isMarkdownTableSeparator(line: string) {
  return /^\|?[\s:-]*---[\s|:-]*\|?$/.test(line);
}

function MarkdownTable({ block }: { block: MarkdownBlock }) {
  const rows = block.lines.map(parseMarkdownTableRow).filter((row) => row.length > 0);
  if (rows.length === 0) {
    return null;
  }
  const [header, ...body] = rows;

  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="min-w-full border-collapse text-left text-sm leading-6">
        <thead className="bg-panel text-ink">
          <tr>
            {header.map((cell, index) => (
              <th
                className="border-b border-line px-3 py-2 font-semibold"
                key={`${cell}-${index}`}
              >
                {renderInlineMarkdown(cell)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, rowIndex) => (
            <tr className="border-t border-line" key={rowIndex}>
              {row.map((cell, cellIndex) => (
                <td
                  className="align-top px-3 py-2 text-ink/72"
                  key={`${rowIndex}-${cellIndex}`}
                >
                  {renderInlineMarkdown(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function parseMarkdownTableRow(line: string) {
  return line
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderInlineMarkdown(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }

    const token = match[0];
    if (token.startsWith("**")) {
      nodes.push(
        <strong className="font-semibold text-ink" key={`${match.index}-strong`}>
          {token.slice(2, -2)}
        </strong>,
      );
    } else {
      nodes.push(
        <code
          className="rounded border border-line bg-panel px-1.5 py-0.5 text-[0.9em] text-ink"
          key={`${match.index}-code`}
        >
          {token.slice(1, -1)}
        </code>,
      );
    }

    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes;
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

function SourceBadge({ source }: { source: string | null }) {
  return (
    <span
      className={[
        "inline-flex h-6 items-center gap-1.5 rounded-full px-2.5 text-[11px] font-semibold uppercase tracking-[0.12em]",
        source === "reddit"
          ? "bg-clay/12 text-clay"
          : source === "omscentral"
            ? "bg-marine/12 text-marine"
            : "bg-ink/10 text-ink/60",
      ].join(" ")}
    >
      <Database className="h-3 w-3" aria-hidden="true" />
      {compactSource(source)}
    </span>
  );
}

function getSourceStats(documents: CourseDocument[]) {
  return documents.reduce(
    (stats, document) => {
      if (document.source === "reddit") {
        stats.reddit += 1;
      } else if (document.source === "omscentral") {
        stats.omscentral += 1;
      }
      stats.chunks += document.chunk_count;
      return stats;
    },
    { omscentral: 0, reddit: 0, chunks: 0 },
  );
}

function normalizePriorityIndex(index: number) {
  return index === -1 ? Number.MAX_SAFE_INTEGER : index;
}

function compareChunks(
  first: QueryResponse["chunks"][number],
  second: QueryResponse["chunks"][number],
  sort: CitationSort,
) {
  if (sort === "match") {
    return second.score - first.score;
  }

  const firstTime = getCitationTime(first.published_at);
  const secondTime = getCitationTime(second.published_at);
  if (firstTime === secondTime) {
    return second.score - first.score;
  }
  return sort === "newest" ? secondTime - firstTime : firstTime - secondTime;
}

function getCitationTime(value: string | null) {
  if (!value) {
    return 0;
  }
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? 0 : time;
}
