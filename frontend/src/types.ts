export interface Project {
  id: number;
  name: string;
  pinned: boolean;
  description: string;
  goals: string;
  status: string;
  repository_path: string | null;
  git_remote: string | null;
  tags: string[];
  architecture_mermaid: string;
  created_at: string;
  updated_at: string;
}
export interface RecordData {
  id: number;
  title?: string;
  status?: string;
  description?: string;
  [key: string]: any;
}
export interface Overview {
  project: Project;
  tasks: RecordData[];
  notes: RecordData[];
  activity: RecordData[];
}
