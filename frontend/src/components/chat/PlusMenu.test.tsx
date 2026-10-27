import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, type ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import type { DocumentConfig, PluginInfo } from "../../api/types";
import {
  makeAdminMe,
  makeCapability,
  makeChatModel,
  makeConnection,
  makeDocumentConfig,
  makeGmailPlugin,
  makeMe,
  makePaidPlanAccess,
} from "../../test/fixtures";
import { deferred, errorResponse, stubFetch, type Route } from "../../test/http";
import { renderWithRouter } from "../../test/render";
import Composer, { type ComposerHandle } from "./Composer";
import PlusMenu from "./PlusMenu";

type PlusProps = ComponentProps<typeof PlusMenu>;

const opus = makeChatModel();
const gpt = makeChatModel({ id: 2, display_name: "GPT-5.5", provider: "openai", is_default: false, can_create_images: false });
const mini = makeChatModel({
  id: 3,
  display_name: "GPT-5.4 mini",
  provider: "openai",
  is_default: false,
  can_create_files: false,
  can_create_images: false,
  supports_tools: false,
});
const MB = 1024 * 1024;

function Harness(overrides: Partial<PlusProps>) {
  const composer = useRef<ComposerHandle>(null);
  const plusRef = useRef<HTMLButtonElement>(null);
  return (
    <Composer
      ref={composer}
      streaming={false}
      disabledReason={null}
      replying={false}
      selection={null}
      canCreateFiles
      canCreateImages
      enterToSend
      attachments={[]}
      onRemoveAttachment={() => {}}
      waiting={null}
      onSend={() => {}}
      onStop={() => {}}
      plusMenu={
        <PlusMenu
          triggerRef={plusRef}
          user={makeMe()}
          models={[opus, gpt, mini]}
          selectedModel={opus}
          composerDisabled={null}
          plugins={[]}
          onStarter={(prefix) => composer.current?.insertStarter(prefix)}
          onAttach={() => {}}
          onImportGmail={() => {}}
          onOpenPluginSettings={() => {}}
          {...overrides}
        />
      }
    />
  );
}

async function setup(overrides: Partial<PlusProps> = {}, config: Route = makeDocumentConfig()) {
  const calls = stubFetch({ "GET /documents/config": config });
  const user = userEvent.setup();
  const view = renderWithRouter(<Harness {...overrides} />);
  const open = async () => {
    await user.click(screen.getByRole("button", { name: "Add files and more" }));
    await screen.findByRole("menu", { name: "Add files and more" });
    await act(async () => {});
  };
  return { user, view, calls, open };
}

const item = (name: string) => screen.getByRole("menuitem", { name });
const queryItem = (name: string) => screen.queryByRole("menuitem", { name });
const FORMATS = ["PDF document", "Word document", "Excel spreadsheet", "Markdown file"];

function expectDisabled(name: string, reason: string) {
  expect(item(name)).toHaveAttribute("aria-disabled", "true");
  expect(item(name)).toHaveAccessibleDescription(reason);
}

function expectEnabled(name: string, description?: string) {
  expect(item(name)).not.toHaveAttribute("aria-disabled");
  if (description !== undefined) expect(item(name)).toHaveAccessibleDescription(description);
}

describe("PlusMenu", () => {
  it("checks the storage each time it opens, then offers every item", async () => {
    const config = deferred<DocumentConfig>();
    const { user, calls } = await setup({}, () => config.promise);
    await user.click(screen.getByRole("button", { name: "Add files and more" }));
    expectDisabled("Attach files…", "Checking your storage…");
    expectDisabled("Create an image", "Checking your storage…");
    FORMATS.forEach((name) => expect(item(name)).toHaveAttribute("aria-disabled", "true"));
    expect(item("PDF document")).toHaveAccessibleDescription("Checking your storage…");

    await act(async () => config.resolve(makeDocumentConfig()));
    expectEnabled("Attach files…", "Upload to My documents · up to 25 MB each");
    expectEnabled("Create an image", "47 of 50 left this period");
    FORMATS.forEach((name) => expectEnabled(name));
    expect(screen.getByRole("group", { name: "Create" })).toContainElement(item("Create an image"));
    expect(screen.getByRole("group", { name: "Create a document" })).toContainElement(item("Excel spreadsheet"));
    expect(calls.filter((call) => call.path === "/documents/config")).toHaveLength(1);

    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Add files and more" }));
    expect(calls.filter((call) => call.path === "/documents/config")).toHaveLength(2);
  });

  it("opens the file picker from Attach files… and attaches the picked files with the upload settings", async () => {
    const onAttach = vi.fn();
    const pickerClick = vi.spyOn(HTMLInputElement.prototype, "click");
    const { user, view, open } = await setup({ onAttach });
    await open();
    await user.click(item("Attach files…"));
    expect(pickerClick).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    const picker = view.container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(picker).toHaveAttribute("accept", makeDocumentConfig().supported_extensions.join(","));
    const file = new File(["%PDF-1.7"], "board-deck.pdf", { type: "application/pdf" });
    await user.upload(picker as HTMLInputElement, file);
    expect(onAttach).toHaveBeenCalledExactlyOnceWith([file], makeDocumentConfig());
  });

  it.each<[string, Partial<DocumentConfig>, string]>([
    ["at the document limit", { document_count: 100 }, "You've reached your limit of 100 documents."],
    ["with full storage", { storage_bytes: 500 * MB }, "Your storage is full."],
  ])("explains that attaching is unavailable %s", async (_, overrides, reason) => {
    const { open } = await setup({}, makeDocumentConfig(overrides));
    await open();
    expectDisabled("Attach files…", reason);
    expectDisabled("Create an image", "Your documents are full.");
    expect(item("PDF document")).toHaveAccessibleDescription("Your documents are full.");
  });

  it("keeps attaching possible but not an image when less than an image's storage is left", async () => {
    const { open } = await setup({}, makeDocumentConfig({ storage_bytes: 495 * MB }));
    await open();
    expectEnabled("Attach files…");
    expectDisabled("Create an image", "Your documents are full.");
    expectEnabled("PDF document");
  });

  it("leaves the decision to the server when the storage check fails", async () => {
    const { open } = await setup({}, errorResponse(503, "Service unavailable"));
    await open();
    expectEnabled("Attach files…", "Upload to My documents");
    expectEnabled("Create an image");
    expectEnabled("Markdown file");
  });

  it("shows why the composer is unavailable on every item", async () => {
    const reason = "Wait for the conversation to load.";
    const { open } = await setup({ composerDisabled: reason });
    await open();
    expectDisabled("Attach files…", reason);
    expectDisabled("Create an image", reason);
    expect(item("Word document")).toHaveAccessibleDescription(reason);
  });

  it("explains which model to pick for images and files", async () => {
    const { user, open, view } = await setup({ selectedModel: gpt });
    await open();
    expectDisabled("Create an image", "GPT-5.5 can't create images. Pick a model marked Images.");
    expectEnabled("PDF document");
    await user.keyboard("{Escape}");

    view.rerender(<Harness selectedModel={mini} />);
    await open();
    expectDisabled("Create an image", "GPT-5.4 mini can't create images. Pick a model marked Images.");
    expect(item("PDF document")).toHaveAttribute("aria-disabled", "true");
    expect(item("PDF document")).toHaveAccessibleDescription("GPT-5.4 mini can't create files. Pick a model marked Files.");
  });

  it("asks to pick a model when none is selected", async () => {
    const { open } = await setup({ selectedModel: null });
    await open();
    expectDisabled("Create an image", "Pick a model first.");
    expect(item("PDF document")).toHaveAccessibleDescription("Pick a model first.");
  });

  it("says when this period's images are used up and when the count starts again", async () => {
    const user = makeMe();
    const { open } = await setup({ user: { ...user, usage: { ...user.usage, images: 50 } } });
    await open();
    expectDisabled("Create an image", "You've used this period's 50 images. The count starts again on 1 Nov.");
  });

  it("dates the images count by the period: the next one, or the plan's end when nothing follows", async () => {
    const paid = makeMe({ plan: makePaidPlanAccess({ features: { files: true, images: true, plugins: [] } }) });
    const usage = { ...paid.usage, images: 50, window_end: "2026-11-07T18:30:00Z" };
    const first = await setup({ user: { ...paid, usage } });
    await first.open();
    expectDisabled("Create an image", "You've used this period's 50 images. Your plan is valid through 7 Nov.");
    first.view.unmount();

    const next = makePaidPlanAccess({ features: { files: true, images: true, plugins: [] }, next_plan: { id: 1, code: "starter", name: "Starter" } });
    const second = await setup({ user: { ...paid, plan: next, usage } });
    await second.open();
    expectDisabled(
      "Create an image",
      "You've used this period's 50 images. The count starts again with your next period on 8 Nov.",
    );
  });

  it("shows features outside the plan as one locked item each, leading to the plans", async () => {
    const starter = makeMe({
      plan: makePaidPlanAccess({
        features: { files: false, images: false, plugins: [] },
        unlock: { files: "Standard", images: "Professional", plugins: { gmail: "Professional" }, models: "Standard" },
      }),
    });
    const { open, user, view } = await setup({ user: starter });
    await open();
    expect(item("Create an image")).not.toHaveAttribute("aria-disabled");
    expect(item("Create an image")).toHaveAccessibleDescription("Included in Professional. Opens the plans page.");
    expect(item("Create a document")).toHaveAccessibleDescription("Included in Standard. Opens the plans page.");
    FORMATS.forEach((name) => expect(queryItem(name)).not.toBeInTheDocument());
    expect(screen.getAllByText("Upgrade")).toHaveLength(2);
    await user.click(item("Create a document"));
    expect(view.location()).toBe("/plans");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("hides a locked feature no plan includes", async () => {
    const plan = makePaidPlanAccess({
      features: { files: true, images: false, plugins: [] },
      unlock: { files: null, images: null, plugins: {}, models: null },
    });
    const { open } = await setup({ user: makeMe({ plan }) });
    await open();
    expect(queryItem("Create an image")).not.toBeInTheDocument();
    expect(item("PDF document")).toBeInTheDocument();
  });

  it("shows Gmail outside the plan as one locked item", async () => {
    const plan = makePaidPlanAccess({ features: { files: true, images: false, plugins: [] } });
    const gmail = makeGmailPlugin({ in_plan: false, connection: makeConnection() });
    const { open } = await setup({ user: makeMe({ plan }), plugins: [gmail] });
    await open();
    expect(screen.getByRole("group", { name: "Gmail" })).toContainElement(item("Search, draft and import email"));
    expect(item("Search, draft and import email")).toHaveAccessibleDescription(
      "Included in Professional. Opens the plans page.",
    );
    expect(queryItem("Search my email")).not.toBeInTheDocument();
  });

  it("doesn't count images for the admin", async () => {
    const { open } = await setup({ user: makeAdminMe() });
    await open();
    expectEnabled("Create an image", "Saved to My documents");
  });

  it("treats zero document and storage limits as unlimited", async () => {
    const { open } = await setup(
      { user: makeAdminMe() },
      makeDocumentConfig({ max_documents: 0, max_storage_mb: 0, document_count: 4_000, storage_bytes: 900 * MB }),
    );
    await open();
    expectEnabled("Attach files…");
    expectEnabled("Create an image");
    expectEnabled("PDF document");
  });

  it("hides what no model can create", async () => {
    const { user, open, view } = await setup({ models: [gpt, mini], selectedModel: gpt });
    await open();
    expect(queryItem("Create an image")).not.toBeInTheDocument();
    expect(item("PDF document")).toBeInTheDocument();
    await user.keyboard("{Escape}");

    view.rerender(<Harness models={[mini]} selectedModel={mini} />);
    await open();
    expect(screen.queryByRole("group", { name: "Create" })).not.toBeInTheDocument();
    FORMATS.forEach((name) => expect(queryItem(name)).not.toBeInTheDocument());
    expect(item("Attach files…")).toBeInTheDocument();
  });

  it("inserts starters into the composer, replacing one already there", async () => {
    const { user, open } = await setup();
    const input = screen.getByRole("textbox", { name: "Message" });
    await open();
    await user.click(item("Create an image"));
    expect(input).toHaveValue("Create an image: ");
    expect(input).toHaveFocus();

    await user.type(input, "a lighthouse at dusk");
    await open();
    await user.click(item("Word document"));
    expect(input).toHaveValue("Create a Word document: a lighthouse at dusk");
    expect(input).toHaveFocus();
  });

  it.each<[string, PluginInfo[]]>([
    ["isn't registered", []],
    ["isn't set up", [makeGmailPlugin({ configured: false, enabled: false, connection: null })]],
    ["is turned off", [makeGmailPlugin({ enabled: false })]],
  ])("hides Gmail when it %s", async (_, plugins) => {
    const { open } = await setup({ plugins });
    await open();
    expect(screen.queryByRole("group", { name: "Gmail" })).not.toBeInTheDocument();
  });

  it("offers to connect Gmail in Settings", async () => {
    const onOpenPluginSettings = vi.fn();
    const { user, open } = await setup({ plugins: [makeGmailPlugin({ connection: null })], onOpenPluginSettings });
    await open();
    expectEnabled("Connect Gmail…", "To search, draft and import email");
    expect(queryItem("Search my email")).not.toBeInTheDocument();
    await user.click(item("Connect Gmail…"));
    expect(onOpenPluginSettings).toHaveBeenCalledExactlyOnceWith("gmail");
  });

  it("offers to reconnect Gmail with the reason", async () => {
    const connection = makeConnection({
      status: "reconnect_needed",
      status_reason: "revoked",
      status_message: "Access was revoked in your Google account.",
    });
    const { open } = await setup({ plugins: [makeGmailPlugin({ connection })] });
    await open();
    expectEnabled("Reconnect Gmail…", "Access was revoked in your Google account.");
  });

  it("starts Gmail searches and drafts in the composer and opens the import browser", async () => {
    const onImportGmail = vi.fn();
    const { user, open } = await setup({ plugins: [makeGmailPlugin()], onImportGmail });
    const input = screen.getByRole("textbox", { name: "Message" });
    await open();
    expect(screen.getByRole("group", { name: "Gmail" })).toContainElement(item("Search my email"));
    expectEnabled("Search my email", "maya@example.com");
    expectEnabled("Draft an email", "Saved in your Gmail drafts for you to review");
    expectEnabled("Import from Gmail…", "Threads and attachments into My documents");

    await user.click(item("Search my email"));
    expect(input).toHaveValue("Search my Gmail: ");
    await open();
    await user.click(item("Draft an email"));
    expect(input).toHaveValue("Draft an email: ");
    await open();
    await user.click(item("Import from Gmail…"));
    expect(onImportGmail).toHaveBeenCalledTimes(1);
  });

  it("disables Gmail search and drafts while Gmail is off in chat", async () => {
    const reason = "Gmail is off in chat. Turn on “Use in chat” in Settings → Plugins & data.";
    const { open } = await setup({ plugins: [makeGmailPlugin({ connection: makeConnection({ chat_enabled: false }) })] });
    await open();
    expectDisabled("Search my email", reason);
    expectDisabled("Draft an email", reason);
    expectEnabled("Import from Gmail…");
  });

  it("asks to grant access the connection lacks", async () => {
    const gmail = makeGmailPlugin({
      capabilities: [
        makeCapability({ granted: false }),
        makeCapability({ id: "draft", name: "Create drafts", granted: false }),
      ],
    });
    const { open } = await setup({ plugins: [gmail] });
    await open();
    expectDisabled("Search my email", "Grant Gmail read access in Settings → Plugins & data.");
    expectDisabled("Draft an email", "Grant Gmail draft access in Settings → Plugins & data.");
    expectDisabled("Import from Gmail…", "Grant Gmail read access in Settings → Plugins & data.");
  });

  it("says when the selected model can't use Gmail", async () => {
    const { open } = await setup({ plugins: [makeGmailPlugin()], selectedModel: mini });
    await open();
    expectDisabled("Search my email", "GPT-5.4 mini can't use Gmail. Pick another model.");
    expectDisabled("Draft an email", "GPT-5.4 mini can't use Gmail. Pick another model.");
  });

  it("hides Gmail items the admin turned off or the plugin doesn't support", async () => {
    const gmail = makeGmailPlugin({
      supports_import: false,
      capabilities: [makeCapability(), makeCapability({ id: "draft", name: "Create drafts", enabled: false, granted: false })],
    });
    const { open } = await setup({ plugins: [gmail] });
    await open();
    expect(item("Search my email")).toBeInTheDocument();
    expect(queryItem("Draft an email")).not.toBeInTheDocument();
    expect(queryItem("Import from Gmail…")).not.toBeInTheDocument();
  });
});
