const BASE = '';  // proxied through Vite dev server

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
}

export interface PhotoDetail extends PhotoSummary {
  sha256: string | null;
  file_size: number | null;
  title: string | null;
  caption: string | null;
  latitude: number | null;
  longitude: number | null;
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
  person_tag_id: number | null;
  person_name: string | null;
  x: number | null;
  y: number | null;
  w: number | null;
  h: number | null;
  status: string;
  region_name: string | null;
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
  },
  tags: {
    list: (params?: { flat?: boolean; search?: string }) => get<TagOut[]>('/api/tags', params),
    create: (body: { name: string; parent_id?: number | null; is_person?: boolean }) =>
      fetch('/api/tags', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json() as Promise<TagOut>),
    delete: (id: number) => fetch(`/api/tags/${id}`, { method: 'DELETE' }),
  },
  photoTags: {
    add: (photoId: number, tagId: number) =>
      fetch(`/api/photos/${photoId}/tags/${tagId}`, { method: 'POST' }),
    remove: (photoId: number, tagId: number) =>
      fetch(`/api/photos/${photoId}/tags/${tagId}`, { method: 'DELETE' }),
  },
  faces: {
    list: (params?: { photo_id?: number; person_tag_id?: number; status?: string }) =>
      get<FaceOut[]>('/api/faces', params),
    update: (faceId: string, body: { person_tag_id?: number | null; status?: string; region_name?: string }) =>
      fetch(`/api/faces/${faceId}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json() as Promise<FaceOut>),
  },
  media: {
    thumbnail: (id: number, size: 'sm' | 'md' | 'lg' = 'md') => `/media/thumbnail/${id}?size=${size}`,
    original: (id: number) => `/media/original/${id}`,
  },
};
