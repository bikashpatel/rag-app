// API shapes — must match SPEC.md section 6 and the backend schemas exactly.

export type UserLimits = {
  monthly_token_limit: number; // 0 = unlimited
  rate_limit_per_min: number; // 0 = unlimited
  max_upload_mb: number;
  max_documents: number; // 0 = unlimited
  max_storage_mb: number; // 0 = unlimited
  monthly_image_limit: number; // images per UTC calendar month; 0 = unlimited (the admin's is 0)
};

export type UsageSummary = {
  month: string; // "YYYY-MM"
  chat_tokens: number;
  embedding_tokens: number;
  vision_tokens: number;
  images: number; // images generated this month
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
  /** The assistant can generate images with this model (CHAT_IMAGE_TOOLS on, a usable image model active, function calling). */
  can_create_images: boolean;
};

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";
export type DocumentScope = "private" | "shared";

export type ImageQuality = "low" | "medium" | "high";

/** Display details of an image the assistant generated in a chat (no prompt: see GeneratedImageDetails). */
export type GeneratedImage = {
  width: number;
  height: number;
  alt_text: string;
  quality: ImageQuality; // as requested
};

/** GET /documents/{id}/image/details: what the image viewer shows besides the image (owner only). */
export type GeneratedImageDetails = {
  prompt: string;
  revised_prompt: string | null;
  model_name: string; // the image model's litellm_model when it was generated
  created_at: string;
};

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
  image: GeneratedImage | null; // a generated image (GET /documents/{id}/image serves it); always null in GET /admin/documents
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

export type GeneratedFileFormat = "pdf" | "docx" | "xlsx" | "md" | "png" | "jpeg" | "webp";
/** What a `file` event is about: a create_document file or a create_image image. */
export type GeneratedFileKind = "file" | "image";

/** A file the assistant created in an answer; it is one of the user's private documents. */
export type GeneratedFile = {
  document_id: number | null; // null once the document was deleted
  filename: string;
  format: GeneratedFileFormat;
  size_bytes: number;
  status: DocumentStatus | null; // the document's status now; null when deleted
  image: GeneratedImage | null; // set for a generated image whose document still exists
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

/**
 * Data of the `file` event: progress of one create_document call (`kind` "file", `call_id` "f1", "f2", …) or one
 * create_image call (`kind` "image", `call_id` "i1", "i2", …) of the answer.
 */
export type FileEventData =
  | {
      // The call started (chars 0, title and format null); then at most every 2 s while it is written (title and
      // format as soon as they appear); once more with the validated title (and a file's format) before the work.
      call_id: string;
      kind: GeneratedFileKind;
      status: "writing";
      title: string | null;
      format: GeneratedFileFormat | null; // always null for images
      chars: number; // characters of the file (or the image prompt) written so far (approximate)
    }
  | {
      // Images only: the arguments are valid and the image model was called (20 to 120 s); the final size is known.
      call_id: string;
      kind: "image";
      status: "generating";
      title: string;
      width: number;
      height: number;
      quality: ImageQuality;
    }
  | {
      call_id: string;
      kind: GeneratedFileKind;
      status: "created";
      file: GeneratedFile; // file.image is set for images
      document: DocumentItem;
      already_existed: boolean; // an identical private document existed and is linked instead (always false for images)
    }
  | {
      call_id: string;
      kind: GeneratedFileKind;
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
  monthly_image_limit: number; // effective: the user's own value, else DEFAULT_MONTHLY_IMAGE_LIMIT; 0 = unlimited
  restrict_models: boolean;
  allowed_model_ids: number[];
  created_at: string;
  last_login_at: string | null;
  usage: { chat_tokens: number; embedding_tokens: number; vision_tokens: number; images: number; cost_usd: number };
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
  monthly_image_limit?: number; // 0 = unlimited; default AdminConfig.defaults.monthly_image_limit
  restrict_models?: boolean;
  allowed_model_ids?: number[];
};

export type AdminUserUpdate = Partial<Omit<AdminUserCreate, "username">>;

/**
 * "vision": reads PDF pages for ingestion; "image": generates images in chat (create_image). The active one of each is
 * the enabled default; neither is ever offered as a chat model.
 */
export type ModelKind = "chat" | "embedding" | "vision" | "image";

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
  image_data_url: string | null; // image models: a data: URL of a small thumbnail of the test image
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
  active_image_model: { id: number; display_name: string } | null;
  queue_length: number;
};

export type UsageReport = {
  month: string;
  totals: {
    chat_tokens: number;
    embedding_tokens: number;
    vision_tokens: number;
    images: number;
    cost_usd: number;
    requests: number; // chat requests only
  };
  by_user: {
    user_id: number;
    username: string;
    chat_tokens: number;
    embedding_tokens: number;
    vision_tokens: number;
    images: number;
    cost_usd: number;
    requests: number;
  }[];
  by_model: {
    model_name: string;
    kind: ModelKind;
    prompt_tokens: number; // image models: prompt text tokens
    completion_tokens: number; // image models: image tokens
    cost_usd: number;
    requests: number; // image models: images
  }[];
  daily: {
    date: string;
    chat_tokens: number;
    embedding_tokens: number;
    vision_tokens: number;
    images: number;
    cost_usd: number;
  }[];
};
