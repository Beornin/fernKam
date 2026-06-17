const BASE = 'http://localhost:8000';  // direct to backend

export interface PhotoSummary {
  id: number;
  digikam_id: number | null;
  filename: string;
  album_path: string;
  taken_at: string | null;
  rating: number;
  color_label: number;
  media_type: string;
  width: number | null;
  height: number | null;
  file_size: number | null;
}

export interface PhotoDetail extends PhotoSummary {
  sha256: string | null;
  imported_at: string | null;
  modified_at: string | null;
  title: string | null;
  caption: string | null;
  latitude: number | null;
  longitude: number | null;
  altitude: number | null;
  exif: Record<string, unknown> | null;
  camera: { id: number; make: string | null; model: string | null } | null;
  lens: { id: number; make: string | null; model: string | null } | null;
  tags: TagOut[];
  faces: FaceOut[];
}

export interface TagOut {
  id: number;
  name: string;
  path: string;
  parent_id: number | null;
  is_person: boolean;
  icon: string | null;
  color: string | null;
  children: TagOut[];
}

export interface FaceOut {
  id: string;
  photo_id: number;
  person_tag_id: number | null;
  person_name: string | null;
  x: number | null;
  y: number | null;
  w: number | null;
  h: number | null;
  status: string;
  region_name: string | null;
  score: number | null;  // best_match_score from sweep
}

export interface BatchDetectResult {
  processed: number;
  faces_found: number;
  suggested: number;
  errors: number;
  details: Array<{ photo_id: number; faces?: number; suggested?: number; error?: string }>;
}

export interface PersonOut {
  id: number;
  tag_id: number;
  name: string;
  face_count: number;
  avatar_face_id: string | null;
}

export interface FaceSuggestion {
  person_id: number;
  person_name: string | null;
  score: number;
  conflict: boolean;
}

export interface FaceWithSuggestions {
  face: FaceOut;
  suggestions: FaceSuggestion[];
}

export interface SimilarFace {
  face_id: string;
  person_tag_id: number | null;
  person_name: string | null;
  score: number;
}

export interface AlbumNode {
  name: string;
  path: string;
  photo_count: number;
  children: AlbumNode[];
}

export interface PhotoPage {
  total: number;
  page: number;
  page_size: number;
  items: PhotoSummary[];
}

async function get<T>(path: string, params?: Record<string, string | number | boolean | null | undefined>): Promise<T> {
  const url = new URL(BASE + path, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    }
  }
  const res = await fetch(url.toString());
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return res.json();
}

export const api = {
  albums: {
    list: () => get<AlbumNode[]>('/api/albums'),
  },
  map: {
    points: (params?: { album_path?: string; tag_id?: number; limit?: number }) =>
      get<Array<{ id: number; lat: number; lon: number; filename: string; taken_at: string | null }>>('/api/photos/map/points', params),
  },
  photos: {
    list: (params: {
      album_path?: string;
      tag_id?: number;
      rating_min?: number;
      media_type?: string;
      search?: string;
      sort?: string;
      page?: number;
      page_size?: number;
    }) => get<PhotoPage>('/api/photos', params),
    get: (id: number) => get<PhotoDetail>(`/api/photos/${id}`),
    patch: (id: number, body: Partial<{ rating: number; color_label: number; title: string; caption: string }>) =>
      fetch(`/api/photos/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json()),
    detectFaces: (id: number) =>
      fetch(`/api/photos/${id}/detect-faces`, { method: 'POST' }).then(r => r.json() as Promise<FaceOut[]>),
    batchDetect: (photoIds: number[]) =>
      fetch('/api/photos/batch-detect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(photoIds) }).then(r => r.json() as Promise<BatchDetectResult>),
    batchDetectAll: () =>
      fetch('/api/photos/batch-detect-all', { method: 'POST' }).then(r => r.json() as Promise<BatchDetectResult>),
    unscannedCount: () => get<{ count: number }>('/api/photos/unscanned-count'),
  },
  tags: {
    list: (params?: { flat?: boolean; search?: string }) => get<TagOut[]>('/api/tags', params),
    create: (body: { name: string; parent_id?: number | null; is_person?: boolean }) =>
      fetch('/api/tags', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json() as Promise<TagOut>),
    update: (id: number, body: { name?: string; parent_id?: number | null }) =>
      fetch(`/api/tags/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json() as Promise<TagOut>),
    delete: (id: number) => fetch(`/api/tags/${id}`, { method: 'DELETE' }),
    removeFromPhotos: (id: number) =>
      fetch(`/api/tags/${id}/from-photos`, { method: 'DELETE' }).then(r => r.json() as Promise<{ removed: number }>),
  },
  photoTags: {
    add: (photoId: number, tagId: number) =>
      fetch(`/api/photos/${photoId}/tags/${tagId}`, { method: 'POST' }),
    remove: (photoId: number, tagId: number) =>
      fetch(`/api/photos/${photoId}/tags/${tagId}`, { method: 'DELETE' }),
  },
  faces: {
    list: (params?: { photo_id?: number; person_tag_id?: number; status?: string; limit?: number; offset?: number }) =>
      get<FaceOut[]>('/api/faces', params),
    unassigned: (params?: { photo_id?: number; limit?: number; offset?: number; has_embedding?: boolean }) =>
      get<FaceOut[]>('/api/faces/unassigned', params),
    unassignedCount: () => get<{ count: number }>('/api/faces/unassigned/count'),
    suggestions: (params?: { limit?: number; offset?: number; sort?: string; status_filter?: string }) =>
      get<FaceWithSuggestions[]>('/api/faces/suggestions', params),
    similar: (faceId: string, params?: { k?: number; confirmed_only?: boolean }) =>
      get<SimilarFace[]>(`/api/faces/${faceId}/similar`, params),
    update: (faceId: string, body: { person_tag_id?: number | null; status?: string; region_name?: string }) =>
      fetch(`/api/faces/${faceId}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json() as Promise<FaceOut>),
    batchAssign: (body: { face_ids: string[]; person_tag_id: number | null; status?: string }) =>
      fetch('/api/faces/batch-assign', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    autoConfirmAll: () =>
      fetch('/api/faces/auto-confirm-all', { method: 'POST' }).then(r => r.json() as Promise<{ task_id: string; status: string }>),
    buildCentroids: () =>
      fetch('/api/faces/build-centroids', { method: 'POST' }).then(r => r.json() as Promise<{ updated: number }>),
    suggestionsCount: (params?: { status_filter?: string }) => get<{ count: number }>('/api/faces/suggestions/count', params),
    delete: (faceId: string) => fetch(`/api/faces/${faceId}`, { method: 'DELETE' }),
  },
  people: {
    list: (params?: { search?: string; limit?: number }) =>
      get<PersonOut[]>('/api/people', params),
    create: (name: string, parent_id?: number | null) =>
      fetch('/api/people', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, parent_id }) }).then(r => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json() as Promise<PersonOut>;
      }),
    rename: (id: number, name: string) =>
      fetch(`/api/people/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }).then(r => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json() as Promise<PersonOut>;
      }),
    delete: (id: number) => fetch(`/api/people/${id}`, { method: 'DELETE' }).then(r => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    }),
    faces: (id: number, params?: { limit?: number; offset?: number }) =>
      get<FaceOut[]>(`/api/people/${id}/faces`, params),
  },
  sync: {
    dbToFile: (body?: { photo_ids?: number[]; album_path?: string; limit?: number }) =>
      fetch('/api/sync/db-to-file', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) }).then(r => r.json() as Promise<{ synced: number; errors: number; total: number }>),
    fileToDB: (body?: { photo_ids?: number[]; album_path?: string; limit?: number }) =>
      fetch('/api/sync/file-to-db', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) }).then(r => r.json() as Promise<{ synced: number; tags_created: number; errors: number; total: number }>),
    status: () => get<{ file_newer_than_db: number[]; db_never_synced_to_file: number[] }>('/api/sync/status'),
    scanFaces: (body?: { album_path?: string; limit?: number }) =>
      fetch('/api/sync/scan-faces', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) }).then(r => r.json() as Promise<{ task_id: string | null; queued: number; message: string }>),
    tasks: () => get<{ tasks: Array<{ id: string; task_type: string; status: string; message: string; started_at: string; completed_at: string | null; progress: any }> }>('/api/sync/tasks'),
    cancelTask: (taskId: string) =>
      fetch(`/api/sync/tasks/${taskId}/cancel`, { method: 'POST' }).then(r => r.json() as Promise<{ cancelled: boolean }>),
  },
  media: {
    thumbnail: (id: number, size: 'sm' | 'md' | 'lg' = 'md') => `/media/thumbnail/${id}?size=${size}`,
    original: (id: number) => `/media/original/${id}`,
    video: (id: number) => `/media/video/${id}`,
  },
  logs: {
    list: (params?: { level?: string; source?: string; q?: string; since?: string; before?: string; limit?: number; offset?: number }) =>
      get<{ items: LogEntry[]; limit: number; offset: number }>('/api/logs', params),
    count: (params?: { level?: string; source?: string; q?: string; since?: string; before?: string }) =>
      get<{ count: number }>('/api/logs/count', params),
    levels: () => get<{ levels: Array<{ level_name: string; count: number }> }>('/api/logs/levels'),
    sources: () => get<{ sources: Array<{ source: string; count: number }> }>('/api/logs/sources'),
    clear: (params?: { level?: string; before?: string }) => {
      const qs = params ? '?' + new URLSearchParams(params as any).toString() : '';
      return fetch(`/api/logs${qs}`, { method: 'DELETE' }).then(r => r.json() as Promise<{ deleted: number }>);
    },
    sinkStats: () => get<{ queued: number; inserted_total: number; updated_total: number; dropped_total: number; last_flush_ms: number; running: boolean }>('/api/logs/sink-stats'),
  },
};

export interface LogEntry {
  id: number;
  ts: string;
  level: number;
  level_name: string;
  source: string;
  logger_name: string | null;
  message: string;
  file: string | null;
  line: number | null;
  func: string | null;
  exc_info: string | null;
  context: Record<string, any> | null;
  fingerprint: string;
  occurrences: number;
  last_seen_at: string;
}
