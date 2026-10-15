// API shapes — must match SPEC.md section 6 and the backend schemas exactly.

export type UserLimits = {
  monthly_token_limit: number; // 0 = unlimited
  rate_limit_per_min: number; // 0 = unlimited
  max_upload_mb: number;
  max_documents: number; // 0 = unlimited
  max_storage_mb: number; // 0 = unlimited
};

export type UsageSummary = {
  month: string; // "YYYY-MM"
  chat_tokens: number;
  embedding_tokens: number;
  vision_tokens: number;
  cost_usd: number; // every kind
  monthly_token_limit: number; // 0 = unlimited; counts chat_tokens only
};

export type Me = {
  id: number;
  username: string;
  is_admin: boolean;
  limits: UserLimits;
  usage: UsageSummary;
};

export type ChatModel = {
  id: number;
  display_name: string;
  provider: string;
  is_default: boolean;
  /** The assistant can create files with this model (CHAT_FILE_TOOLS on and the model supports function calling). */
  can_create_files: boolean;
};

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";
export type DocumentScope = "private" | "shared";

export type DocumentItem = {
  id: number;
  filename: string;
  scope: DocumentScope;
  status: DocumentStatus;
  error: string | null;
  size_bytes: number;
  content_type: string;
  num_chunks: number;
  num_pages: number | null;
  owner_id: number;
  owner_username: string;
  can_delete: boolean;
  is_stale: boolean;
  vision_pages: number; // pages read with the vision model; 0 unless status is "ready"
  generated: boolean; // the assistant created this file in a chat
  created_at: string;
  updated_at: string;
};

export type DocumentConfig = {
  supported_extensions: string[];
  max_upload_mb: number;
  max_documents: number; // 0 = unlimited
  max_storage_mb: number; // 0 = unlimited
  document_count: number;
  storage_bytes: number;
  can_upload_shared: boolean;
};

/** null = all accessible documents; number[] = only these ids ([] = don't use documents). */
export type DocumentSelection = number[] | null;

export type Source = {
  index: number; // 1-based citation number, matches [n] in the answer
  document_id: number;
  filename: string;
  page: number | null;
  chunk_index: number;
  score: number;
  snippet: string;
};

export type ConversationSummary = {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
};

export type GeneratedFileFormat = "pdf" | "docx" | "xlsx" | "md";

/** A file the assistant created in an answer; it is one of the user's private documents. */
export type GeneratedFile = {
  document_id: number | null; // null once the document was deleted
  filename: string;
  format: GeneratedFileFormat;
  size_bytes: number;
  status: DocumentStatus | null; // the document's status now; null when deleted
};

export type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  content: string;
  sources: Source[];
  model_name: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  error: string | null;
  files: GeneratedFile[]; // in creation order; [] for user messages
  created_at: string;
};

export type ConversationDetail = ConversationSummary & { messages: ChatMessage[] };

export type ChatStreamRequest = {
  conversation_id: number | null;
  message: string; // 1..20000 chars
  model_id: number | null; // null = default chat model
  document_ids: DocumentSelection; // at most 10000 ids
};

export type TokenUsage = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
};

/** Data of the `file` event: progress of one create_document call. `call_id` is "f1", "f2", … per answer. */
export type FileEventData =
  | {
      // The call started (chars 0, title and format null); then at most every 2 s while it is written (title and
      // format as soon as they appear); once more with the validated title and format just before rendering.
      call_id: string;
      status: "writing";
      title: string | null;
      format: GeneratedFileFormat | null;
      chars: number; // characters of the file written so far (approximate)
    }
  | {
      call_id: string;
      status: "created";
      file: GeneratedFile;
      document: DocumentItem;
      already_existed: boolean; // an identical private document existed and is linked instead
    }
  | {
      call_id: string;
      status: "failed";
      title: string | null;
      format: GeneratedFileFormat | null;
      error: string;
    };

// Server-sent events of POST /api/chat/stream
export type ChatStreamEvent =
  | {
      event: "meta";
      data: {
        conversation_id: number;
        title: string;
        user_message_id: number;
        model: { id: number; display_name: string };
      };
    }
  | { event: "sources"; data: { sources: Source[] } }
  | { event: "warning"; data: { message: string } }
  | { event: "delta"; data: { content: string } }
  | { event: "file"; data: FileEventData }
  | { event: "done"; data: { assistant_message_id: number; usage: TokenUsage } } // usage sums every model call
  | { event: "error"; data: { message: string; assistant_message_id: number | null } };

// ---------------- Admin ----------------

export type AdminUser = {
  id: number;
  username: string;
  is_admin: boolean;
  is_active: boolean;
  monthly_token_limit: number;
  rate_limit_per_min: number;
  max_upload_mb: number;
  max_documents: number;
  max_storage_mb: number;
  restrict_models: boolean;
  allowed_model_ids: number[];
  created_at: string;
  last_login_at: string | null;
  usage: { chat_tokens: number; embedding_tokens: number; vision_tokens: number; cost_usd: number };
  document_count: number;
  storage_bytes: number;
};

export type AdminUserCreate = {
  username: string;
  password: string;
  is_active?: boolean;
  monthly_token_limit?: number;
  rate_limit_per_min?: number;
  max_upload_mb?: number;
  max_documents?: number;
  max_storage_mb?: number;
  restrict_models?: boolean;
  allowed_model_ids?: number[];
};

export type AdminUserUpdate = Partial<Omit<AdminUserCreate, "username">>;

/** "vision": reads PDF pages for ingestion (the active one is the enabled default); never offered for chat. */
export type ModelKind = "chat" | "embedding" | "vision";

export type AdminModel = {
  id: number;
  display_name: string;
  litellm_model: string;
  kind: ModelKind;
  enabled: boolean;
  is_default: boolean;
  api_base: string | null;
  api_key_env: string | null;
  max_output_tokens: number | null;
  temperature: number | null;
  input_cost_per_mtok: number | null;
  output_cost_per_mtok: number | null;
  embedding_dim: number | null;
  created_at: string;
  updated_at: string;
};

export type AdminModelCreate = {
  display_name: string;
  litellm_model: string;
  kind: ModelKind;
  enabled?: boolean;
  is_default?: boolean;
  api_base?: string | null;
  api_key_env?: string | null;
  max_output_tokens?: number | null;
  temperature?: number | null;
  input_cost_per_mtok?: number | null;
  output_cost_per_mtok?: number | null;
};

export type AdminModelUpdate = Partial<Omit<AdminModelCreate, "kind">>;

export type ModelTestResult = {
  ok: boolean;
  message: string;
  latency_ms: number;
  embedding_dim: number | null;
};

export type AdminConfig = {
  defaults: UserLimits;
  max_upload_mb_hard: number;
  admin_username: string;
};

export type AdminStats = {
  users: number;
  documents: number;
  stale_documents: number;
  failed_documents: number;
  active_embedding_model: { id: number; display_name: string } | null;
  active_vision_model: { id: number; display_name: string } | null;
  queue_length: number;
};

export type UsageReport = {
  month: string;
  totals: {
    chat_tokens: number;
    embedding_tokens: number;
    vision_tokens: number;
    cost_usd: number;
    requests: number; // chat requests only
  };
  by_user: {
    user_id: number;
    username: string;
    chat_tokens: number;
    embedding_tokens: number;
    vision_tokens: number;
    cost_usd: number;
    requests: number;
  }[];
  by_model: {
    model_name: string;
    kind: ModelKind;
    prompt_tokens: number;
    completion_tokens: number;
    cost_usd: number;
    requests: number;
  }[];
  daily: {
    date: string;
    chat_tokens: number;
    embedding_tokens: number;
    vision_tokens: number;
    cost_usd: number;
  }[];
};
