// Run with: node tests/test_study_ui.cjs
// Exercise real handlers with a small DOM stand-in; no browser dependencies.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const elements = new Map();
const element = (id) => {
  if (!elements.has(id)) elements.set(id, {
    innerHTML: "", files: [], focus() {}, querySelectorAll: () => [],
  });
  return elements.get(id);
};
const documents = [7, 8, 9].map((id) => ({
  id, filename: `<notes${id}>.pdf`, page_count: 4, question_pages: "", snippet_pages: "", blank_pages: [],
}));
let savedSources = {
  theory_document_id: 7, theory_pages: "1", question_document_id: null, question_pages: "2",
  same_question_pdf: true, answer_document_id: null, answer_pages: "4", answer_pdf: "questions",
};
const quiz = {
  filename: "<questions>.pdf", pages: [2], answer_filename: "<answers>.pdf", answer_pages: [4],
  questions: [
    { prompt: "What is <theory>?\nA) First\nB) Second", answer: "A) <First>",
      question_excerpts: [{ page: 2, text: "What is <theory>?\nA) First\nB) Second" }],
      answer_excerpts: [{ page: 4, text: "A) <First>" }] },
    { prompt: "Explain theory.", answer: "", question_excerpts: [{ page: 2, text: "Explain theory." }], answer_excerpts: [] },
  ],
};
const calls = [];
let authReply = { id: 1, lessons_left_today: 2 };
let quizError = "";
let authError = "";
const context = {
  state: { user: { id: 1 }, tab: "study", study: null },
  view: element("view"), document: { getElementById: element },
  window: { confirm: () => true }, FormData,
  fetch: async (url, options) => {
    calls.push({ url, ...options });
    if (url === "/api/auth/me" && authError) throw new Error(authError);
    if (url.endsWith("/questions") && quizError) {
      return { status: 502, ok: false, json: async () => ({ detail: quizError }) };
    }
    let reply;
    if (url === "/api/study/sources") {
      if (options.method === "PUT") savedSources = JSON.parse(options.body);
      reply = { ...savedSources };
    } else if (url === "/api/auth/me") reply = authReply;
    else if (url.endsWith("/questions")) reply = quiz;
    else if (url.endsWith("/snippet")) reply = { text: "<script>theory</script>", page: 1, filename: "<notes7>.pdf" };
    else if (options.method === "POST") reply = { ...documents[0], id: 10 };
    else reply = documents;
    return { status: 200, ok: true, json: async () => reply };
  },
};
vm.createContext(context);
vm.runInContext(
  source.slice(source.indexOf("async function api("), source.indexOf("// Card bodies"))
  + source.slice(source.indexOf("async function renderStudy("), source.indexOf("/* --- sharing --- */")), context,
);
const change = (id, value, checkbox = false) => {
  element(id)[checkbox ? "checked" : "value"] = value;
  element(id).onchange({ target: element(id) });
};
const type = (id, value) => element(id).oninput({ target: { value } });
const lastQuestion = () => calls.findLast((call) => call.url.endsWith("/questions"));
async function run(id, expectedError = "", submit = false) {
  element(id)[submit ? "onsubmit" : "onclick"]({ preventDefault() {} });
  for (let tries = 0; context.state.study.pending && tries < 10; tries++) await new Promise(setImmediate);
  assert.equal(context.state.study.pending, "", "request should settle");
  assert.equal(context.state.study.error, expectedError);
}

(async () => {
  await context.renderStudy();
  assert.equal(calls[0].url, "/api/study/documents");
  assert.equal(calls[1].url, "/api/study/sources");
  assert.match(context.view.innerHTML, /&lt;notes7&gt;\.pdf/);
  await run("studyExtractButton");
  assert.equal(lastQuestion().url, "/api/study/documents/7/questions");
  assert.deepEqual(JSON.parse(lastQuestion().body), { pages: "2", answer_document_id: 7, answer_pages: "4", count: 3 });
  assert.equal(calls.at(-3).url, "/api/study/sources", "sources save before extraction");
  assert.equal(context.state.user.lessons_left_today, 2);
  assert.match(element("studyQuiz").innerHTML, /What is &lt;theory&gt;\?/);
  assert.match(element("studyQuiz").innerHTML, /A\) First\nB\) Second/);
  assert.match(element("studyQuiz").innerHTML, /A\) &lt;First&gt;/);
  assert.match(element("studyQuiz").innerHTML, /&lt;answers&gt;\.pdf · PDF pages 4/);
  assert.match(element("studyQuiz").innerHTML, /No matching answer found in the selected answer pages\./);
  assert.doesNotMatch(element("studyQuiz").innerHTML, /Correct answer|Not quite|data-study-choice/);

  change("studySameQuestionPdf", false, true);
  assert.equal(context.state.study.quiz, null, "source changes discard stale questions");
  change("studyQuestionDocument", "8");
  change("studyAnswerSource", "separate");
  change("studyAnswerDocument", "9");
  type("studyQuestionPages", "1-2");
  type("studyAnswerPages", "3-4");
  await run("studySourcesForm", "", true);
  assert.deepEqual(savedSources, {
    theory_document_id: 7, theory_pages: "1", question_document_id: 8, question_pages: "1-2",
    same_question_pdf: false, answer_document_id: 9, answer_pages: "3-4", answer_pdf: "separate",
  });
  await run("studyExtractButton");
  assert.equal(lastQuestion().url, "/api/study/documents/8/questions");
  assert.deepEqual(JSON.parse(lastQuestion().body), { pages: "1-2", answer_document_id: 9, answer_pages: "3-4", count: 3 });
  change("studyAnswerSource", "theory");
  await run("studyExtractButton");
  assert.equal(JSON.parse(lastQuestion().body).answer_document_id, 7);
  change("studySameQuestionPdf", true, true);
  change("studyAnswerSource", "questions");
  await run("studyExtractButton");
  assert.equal(lastQuestion().url, "/api/study/documents/7/questions");
  assert.equal(JSON.parse(lastQuestion().body).answer_document_id, 7);

  const scratch = element("studyScratch0");
  scratch.dataset = { studyScratch: "0" };
  element("studyQuiz").querySelectorAll = () => [scratch];
  context.paintStudyQuiz(context.state.study);
  scratch.value = "My <working>";
  scratch.oninput();
  await run("studySnippetButton");
  assert.equal(calls.at(-1).url, "/api/study/documents/7/snippet");
  assert.deepEqual(JSON.parse(calls.at(-1).body), { pages: "1" });
  assert.match(context.view.innerHTML, /&lt;script&gt;theory&lt;\/script&gt;/);
  assert.match(element("studyQuiz").innerHTML, /My &lt;working&gt;/);

  quizError = "Extraction unavailable";
  authReply = { id: 1, lessons_left_today: 1 };
  await run("studyExtractButton", quizError);
  assert.equal(context.state.user.lessons_left_today, 1);
  authError = "Refresh unavailable";
  await run("studyExtractButton", quizError);
  quizError = authError = "";

  change("studyDocument", "8");
  await run("studyDelete");
  assert.equal(context.state.study.sources.question_document_id, null);
  assert.equal(context.state.study.sources.question_pages, "1-2", "deleting an inactive remembered PDF preserves linked ranges");
  change("studyDocument", "7");
  await run("studyDelete");
  assert.equal(context.state.study.sources.theory_document_id, null);
  assert.equal(context.state.study.sources.question_pages, "");
  assert.equal(context.state.study.sources.answer_pages, "");
  await run("studyExtractButton", "Choose a questions PDF and question pages.");

  element("studyFile").files = [new Blob(["%PDF-"], { type: "application/pdf" })];
  await run("studyUpload", "", true);
  assert(calls.at(-1).body instanceof FormData);
  assert.equal(calls.at(-1).headers["Content-Type"], undefined);
  assert.equal(context.state.study.sources.theory_document_id, 10);
  type("studyQuestionPages", "2");
  type("studyAnswerPages", "4");

  const regularFetch = context.fetch;
  let complete;
  context.fetch = (url, options) => url === "/api/auth/me"
    ? new Promise((resolve) => { complete = () => resolve({ status: 200, ok: true, json: async () => authReply }); })
    : regularFetch(url, options);
  element("studyExtractButton").onclick();
  await new Promise(setImmediate);
  assert.equal(typeof complete, "function");
  context.state.study = null;
  context.state.user = null;
  context.view.innerHTML = "Signed out";
  complete();
  await new Promise(setImmediate);
  assert.equal(context.view.innerHTML, "Signed out");
  assert.equal(context.state.user, null, "late quota refresh must not restore a signed-out user");

  context.state.user = { id: 2 };
  context.fetch = async () => { throw new Error("Offline"); };
  await context.renderStudy();
  assert.match(context.view.innerHTML, /Offline/);
  assert.match(context.view.innerHTML, /<fieldset class="study-fields" disabled>/);
  assert.match(context.view.innerHTML, /Retry loading PDFs/);
  console.log("Study UI: linked and separate sources, extraction-only display, persistence, escaping, and session guards passed.");
})().catch((error) => { console.error(error); process.exitCode = 1; });
