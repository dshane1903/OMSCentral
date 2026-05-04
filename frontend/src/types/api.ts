export type Course = {
  course_id: string;
  slug: string;
  name: string;
  codes: string[];
  credit_hours: number | null;
  description: string | null;
  rating: number | null;
  difficulty: number | null;
  workload: number | null;
  review_count: number;
  official_url: string | null;
  syllabus_url: string | null;
  source: string;
  metadata: Record<string, unknown>;
};

export type RetrievedChunk = {
  document_id: string;
  chunk_index: number;
  score: number;
  text: string;
  source: string | null;
  document_type: string | null;
  title: string | null;
  url: string | null;
  course_slug: string | null;
  course_name: string | null;
  course_codes: string[];
  published_at: string | null;
};

export type QueryResponse = {
  answer: string;
  chunks: RetrievedChunk[];
};

export type CourseListResponse = {
  courses: Course[];
};
