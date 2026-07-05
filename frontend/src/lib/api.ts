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
  duration_secs: number | null;
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

export interface ClusterFace {
  id: string;
  photo_id: number;
}

export interface FaceCluster {
  cluster_id: number;
  size: number;
  faces: ClusterFace[];
  suggested_person: { person_id: number; person_name: string | null; score: number } | null;
}

export interface ClusterPage {
  clusters: FaceCluster[];
  total: number;
}

export interface AlbumNode {
  name: string;
  path: string;
  photo_count: number;
  children: AlbumNode[];
}

export interface SavedSearchOut {
  id: number;
  name: string;
  description: string | null;
  filters: Record<string, unknown>;
  sort: string;
  pin_to_sidebar: boolean;
  created_at: string;
  updated_at: string;
}

export interface PhotoPage {
  total: number;
  page: number;
  page_size: number;
  items: PhotoSummary[];
  next_cursor?: string | null;
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
      person_tag_id?: number;
      rating_min?: number;
      color_label?: number;
      media_type?: string;
      camera_id?: number;
      lens_id?: number;
      has_gps?: boolean;
      has_faces?: boolean;
      no_date?: boolean;
      search?: string;
      date_from?: string;
      date_to?: string;
      sort?: string;
      page?: number;
      page_size?: number;
      cursor?: string;
    }) => get<PhotoPage>('/api/photos', params),
    get: (id: number) => get<PhotoDetail>(`/api/photos/${id}`),
    patch: (id: number, body: Partial<{ rating: number; color_label: number; title: string; caption: string }>) =>
      fetch(`/api/photos/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json()),
    detectFaces: (id: number) =>
      fetch(`/api/photos/${id}/detect-faces`, { method: 'POST' }).then(r => r.json() as Promise<FaceOut[]>),
    batchDetect: (photoIds: number[]) =>
      fetch('/api/photos/batch-detect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(photoIds) }).then(r => r.json() as Promise<BatchDetectResult>),
    timeline: () => get<{ years: Array<{ year: number; count: number; months: Array<{ month: number; count: number }> }> }>('/api/photos/timeline'),
    inferDatesPreview: (params?: { limit?: number; offset?: number }) =>
      get<{ total_undated: number; offset: number; limit: number; candidates: Array<{ id: number; filename: string; album_path: string; inferred_date: string; source: string }> }>('/api/photos/infer-dates/preview', params),
    inferDatesApply: (ids: number[]) =>
      fetch('/api/photos/infer-dates/apply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }) }).then(r => r.json() as Promise<{ updated: number; skipped: number }>),
    batchEdit: (ids: number[], fields: Partial<{ rating: number; color_label: number; title: string; caption: string }>) =>
      fetch('/api/photos/batch-edit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids, ...fields }) }).then(r => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json() as Promise<{ updated: number }>;
      }),
    cameras: () => get<Array<{ id: number; make: string | null; model: string | null; label: string }>>('/api/photos/cameras'),
    lenses: () => get<Array<{ id: number; make: string | null; model: string | null; label: string }>>('/api/photos/lenses'),
    batchDetectAll: () =>
      fetch('/api/photos/batch-detect-all', { method: 'POST' }).then(r => r.json() as Promise<BatchDetectResult>),
    unscannedCount: () => get<{ count: number }>('/api/photos/unscanned-count'),
    trash: (id: number) =>
      fetch(`${BASE}/api/photos/${id}/trash`, { method: 'POST' }).then(r => r.json() as Promise<{ ok: boolean; filename: string }>),
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
      get<FaceOut[]>('/api/faces/', params),
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
    autoConfirmIncremental: () =>
      fetch('/api/faces/auto-confirm-incremental', { method: 'POST' }).then(r => r.json() as Promise<{ task_id: string; status: string; since: string | null }>),
    archiveLowQuality: () =>
      fetch('/api/faces/archive-low-quality', { method: 'POST' }).then(r => r.json() as Promise<{ archived: number; min_det_score: number; min_face_px: number }>),
    buildCentroids: () =>
      fetch('/api/faces/build-centroids', { method: 'POST' }).then(r => r.json() as Promise<{ updated: number }>),
    suggestionsCount: (params?: { status_filter?: string }) => get<{ count: number }>('/api/faces/suggestions/count', params),
    delete: (faceId: string) => fetch(`/api/faces/${faceId}`, { method: 'DELETE' }),
    clustersRebuild: (params?: { min_size?: number; cluster_thresh?: number; k?: number }) => {
      const qs = new URLSearchParams();
      if (params?.min_size != null) qs.set('min_size', String(params.min_size));
      if (params?.cluster_thresh != null) qs.set('cluster_thresh', String(params.cluster_thresh));
      if (params?.k != null) qs.set('k', String(params.k));
      const q = qs.toString();
      return fetch(`/api/faces/clusters/rebuild${q ? `?${q}` : ''}`, { method: 'POST' })
        .then(r => r.json() as Promise<{ task_id: string; status: string; threshold: number }>);
    },
    clusters: (params?: { offset?: number; limit?: number }) =>
      get<ClusterPage>('/api/faces/clusters', params),
    clustersAutoAssign: (params?: { min_score?: number; min_agreement?: number; sample_k?: number; dry_run?: boolean }) => {
      const qs = new URLSearchParams();
      if (params?.min_score != null) qs.set('min_score', String(params.min_score));
      if (params?.min_agreement != null) qs.set('min_agreement', String(params.min_agreement));
      if (params?.sample_k != null) qs.set('sample_k', String(params.sample_k));
      if (params?.dry_run != null) qs.set('dry_run', String(params.dry_run));
      const q = qs.toString();
      return fetch(`/api/faces/clusters/auto-assign${q ? `?${q}` : ''}`, { method: 'POST' })
        .then(r => r.json() as Promise<{ task_id: string; status: string; threshold: number; dry_run: boolean }>);
    },
    batchDelete: (face_ids: string[]) =>
      fetch('/api/faces/batch-delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(face_ids) }),
    ignoreTiny: (max_size = 50) =>
      fetch(`/api/faces/ignore-tiny?max_size=${max_size}`, { method: 'POST' }).then(r => r.json() as Promise<{ ignored: number; max_size: number }>),
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
    faces: (id: number, params?: { limit?: number; offset?: number; sort?: string; dir?: string }) =>
      get<FaceOut[]>(`/api/people/${id}/faces`, params),
    merge: (id: number, into_id: number) =>
      fetch(`/api/people/${id}/merge`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ into_id }) }).then(r => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json() as Promise<{ merged: number; into: string; face_count: number }>;
      }),
    split: (id: number, face_ids: string[], new_name?: string, into_id?: number) =>
      fetch(`/api/people/${id}/split`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ face_ids, new_name: new_name ?? null, into_id: into_id ?? null }) }).then(r => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json() as Promise<{ split: number; into: string; target_id: number }>;
      }),
  },
  sync: {
    dbToFile: (body?: { photo_ids?: number[]; album_path?: string; limit?: number }) =>
      fetch('/api/sync/db-to-file', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) }).then(r => r.json() as Promise<{ synced: number; errors: number; total: number }>),
    fileToDB: (body?: { photo_ids?: number[]; album_path?: string; limit?: number }) =>
      fetch('/api/sync/file-to-db', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) }).then(r => r.json() as Promise<{ synced: number; tags_created: number; errors: number; total: number }>),
    refreshMetadata: (body?: { album_path?: string; photo_ids?: number[] }) =>
      fetch('/api/sync/refresh-metadata', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) }).then(r => r.json() as Promise<{ status: string; task_id: string | null; queued: number; message: string }>),
    status: () => get<{ file_newer_than_db: number[]; db_never_synced_to_file: number[] }>('/api/sync/status'),
    scanFaces: (body?: { album_path?: string; limit?: number }) =>
      fetch('/api/sync/scan-faces', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) }).then(r => r.json() as Promise<{ task_id: string | null; queued: number; message: string }>),
    tasks: () => get<{ tasks: Array<{ id: string; task_type: string; status: string; message: string; started_at: string; completed_at: string | null; progress: any }> }>('/api/sync/tasks'),
    cancelTask: (taskId: string) =>
      fetch(`/api/sync/tasks/${taskId}/cancel`, { method: 'POST' }).then(r => r.json() as Promise<{ cancelled: boolean }>),
    scanLibrary: (body?: { custom_path?: string; limit?: number }) =>
      fetch('/api/sync/scan-library', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) }).then(r => r.json() as Promise<{ status: string; task_id: string; message: string }>),
    backfillVideoDuration: () =>
      fetch('/api/sync/backfill-video-duration', { method: 'POST' }).then(r => r.json() as Promise<{ task_id: string; status: string; total: number }>),
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
  geocode: {
    stats: () => get<{ with_gps: number; geocoded: number; pending: number }>('/api/geocode/stats'),
    run: (params?: { limit?: number; force?: boolean }) =>
      fetch('/api/geocode/run?' + new URLSearchParams(params as any ?? {}).toString(), { method: 'POST' })
        .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json() as Promise<{ task_id: string; status: string }>; }),
    countries: () => get<Array<{ country_code: string; state: string; photo_count: number }>>('/api/geocode/countries'),
  },
  dedup: {
    stats: () => get<{ hashed: number; unhashed: number; total: number; duplicate_groups: number; wasted_bytes: number }>('/api/dedup/stats'),
    computeHashes: (params?: { limit?: number }) =>
      fetch('/api/dedup/compute-hashes' + (params?.limit ? `?limit=${params.limit}` : ''), { method: 'POST' }).then(r => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json() as Promise<{ task_id: string; status: string }>;
      }),
    groups: (params?: { page?: number; page_size?: number; min_count?: number }) =>
      get<{ total_groups: number; page: number; page_size: number; groups: Array<{ sha256: string; count: number; photos: Array<{ id: number; filename: string; album_path: string; taken_at: string | null; file_size: number | null }> }> }>('/api/dedup/groups', params),
  },
  savedSearches: {
    list: (params?: { pinned_only?: boolean }) => get<SavedSearchOut[]>('/api/saved-searches', params),
    create: (body: { name: string; description?: string; filters: Record<string, unknown>; sort?: string; pin_to_sidebar?: boolean }) =>
      fetch('/api/saved-searches', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json() as Promise<SavedSearchOut>;
      }),
    update: (id: number, body: Partial<{ name: string; description: string; filters: Record<string, unknown>; sort: string; pin_to_sidebar: boolean }>) =>
      fetch(`/api/saved-searches/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json() as Promise<SavedSearchOut>;
      }),
    delete: (id: number) => fetch(`/api/saved-searches/${id}`, { method: 'DELETE' }).then(r => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    }),
    photos: (id: number, params?: { page?: number; page_size?: number; cursor?: string }) =>
      get<PhotoPage>(`/api/saved-searches/${id}/photos`, params),
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
