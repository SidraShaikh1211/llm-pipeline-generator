import {
  CreateJobRequest,
  JobEventResponse,
  JobListResponse,
  JobResponse,
} from "../types/api";
import { request } from "./client";

export const listJobs = () => request<JobListResponse>("/api/jobs");

export const getJob = (jobId: string) =>
  request<JobResponse>(`/api/jobs/${jobId}`);

export const createJob = (body: CreateJobRequest) =>
  request<JobResponse>("/api/jobs", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const replayEvents = (jobId: string) =>
  request<JobEventResponse[]>(`/api/jobs/${jobId}/events/replay`);

/** Returns an EventSource for /events. Caller is responsible for closing. */
export const openEventStream = (jobId: string): EventSource =>
  new EventSource(`/api/jobs/${jobId}/events`);
