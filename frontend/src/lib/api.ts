import type {
  Course,
  CourseDocumentsResponse,
  CourseListResponse,
  QueryResponse,
} from "../types/api";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ||
  (import.meta.env.PROD ? window.location.origin : "http://localhost:8000");

async function requestJson<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed with ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function askQuestion(question: string, topK = 5): Promise<QueryResponse> {
  return requestJson<QueryResponse>("/query", {
    method: "POST",
    body: JSON.stringify({
      question,
      top_k: topK,
    }),
  });
}

export function listCourses(): Promise<CourseListResponse> {
  return requestJson<CourseListResponse>("/courses");
}

export function getCourse(slug: string): Promise<Course> {
  return requestJson<Course>(`/courses/${slug}`);
}

export function listCourseDocuments(
  slug: string,
): Promise<CourseDocumentsResponse> {
  return requestJson<CourseDocumentsResponse>(`/courses/${slug}/documents`);
}
