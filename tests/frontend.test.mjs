import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import { webcrypto } from "node:crypto";

const source = await readFile(new URL("../web/storyboard.js", import.meta.url), "utf8");
async function load(app, extras = {}, api = {}) {
    const context = vm.createContext({queueMicrotask, structuredClone, console, JSON, Set, Map, URLSearchParams, TextEncoder, crypto: webcrypto, ...extras});
    const module = new vm.SourceTextModule(source + "\nexport { installFold, mountGraph, changed, generate, installFrame, installDialogue, analysisContext, startAnalysis, showAnalysisState, poll, panelWidget, activityStatus, activityLines, message, createActivityPanel, buildBoard, applyFrameAspect, batchProgress, addSceneAngle, addVideoNode, nodeSignature, upgradeSignature };", {context});
    await module.link(async specifier => {
        const name = specifier.includes("app.js") ? "app" : "api";
        return new vm.SyntheticModule([name], function () { this.setExport(name, name === "app" ? app : api); }, {context});
    });
    await module.evaluate();
    return module.namespace;
}
function graphMock() {
    let id = 0, linkId = 0;
    const graph = {_nodes: [], links: {}, add(node) { node.id = ++id; this._nodes.push(node); },
        remove(node) { this._nodes = this._nodes.filter(n => n !== node); for (const [key, link] of Object.entries(this.links)) if (link.origin_id === node.id || link.target_id === node.id) delete this.links[key]; },
        setDirtyCanvas() {}, getNodeById(id) { return this._nodes.find(n => n.id == id); }};
    const LiteGraph = {createNode(type) {
        return {type, properties: {}, widgets: ["prompt", "job_id", "element_id", "aspect_ratio", "resolution", "quality", "image"].map(name => ({name, value: ""})), inputs: [],
            findInputSlot(name) { return this.inputs.findIndex(i => i.name === name); },
            serialize() { return {id: this.id, type: this.type, properties: structuredClone(this.properties), widgets_values: this.widgets.map(w => w.value)}; },
            addInput(name, type) { this.inputs.push({name, type}); }, setDirtyCanvas() {},
            connect(output, target, slot) { const id = ++linkId; graph.links[id] = {origin_id: this.id, origin_slot: output, target_id: target.id}; target.inputs[slot].link = id; },
            disconnectInput(slot) { const input = this.inputs[slot]; if (input?.link != null) { delete graph.links[input.link]; input.link = null; } },
        };
    }};
    return {graph, LiteGraph};
}
test("generated storyboard frame connects to a local MiniMax H3 video and save node", async () => {
    const {graph, LiteGraph} = graphMock(); graph.change = () => {};
    const frame = LiteGraph.createNode("AstroGateStoryboardFrame"); graph.add(frame);
    frame.pos = [0, 0]; frame.size = [430, 400];
    frame.properties = {astrogate_kind: "shot", astrogate_images: [{filename: "frame.png"}],
        astrogate_video_prompt: "人物走向门口。严禁 BGM 和字幕。"};
    const app = {registerExtension() {}, graph, canvas: {ds: {}, centerOnNode() {}, selectNode() {}}};
    const {addVideoNode} = await load(app, {LiteGraph});
    const video = await addVideoNode(frame);
    assert.equal(video.type, "AstroGateMiniMaxH3Video");
    assert.equal(video.inputs.find(i => i.name === "first_frame").link != null, true);
    assert.equal(video.widgets.find(w => w.name === "prompt").value, frame.properties.astrogate_video_prompt);
    assert.equal(graph._nodes.find(n => n.type === "SaveVideo").inputs.find(i => i.name === "video").link != null, true);
    assert.equal(await addVideoNode(frame), video);
    assert.equal(graph._nodes.length, 3);
});
test("vertical shot layout migration keeps completed images and edited prompts", async () => {
    const {graph, LiteGraph} = graphMock(); graph.change = () => {};
    const board = LiteGraph.createNode("AstroGateStoryboard"); board.pos = [0, 0]; graph.add(board);
    const app = {registerExtension() {}, graph, nodeOutputs: {}};
    const {mountGraph} = await load(app, {LiteGraph});
    const item = (id, pos, version) => ({id, type: "AstroGateStoryboardFrame", title: id, pos,
        widgets: {prompt: id, job_id: "job-" + id, aspect_ratio: "16:9", resolution: "4K", quality: "high"},
        properties: {astrogate_project: "p", astrogate_owner: id, astrogate_key: id, astrogate_kind: "shot",
            astrogate_layout_version: version, astrogate_base_prompt: id, astrogate_images: [{filename: id + ".png"}]}});
    const old = {project_id: "p", chapter_id: "c", layout_version: 4, reference_wiring_version: 4,
        nodes: [item("s1", [500, 80], 4), item("s2", [1000, 80], 4)], links: []};
    const first = mountGraph(board, old).get("s1");
    first.widgets.find(w => w.name === "prompt").value = "人工修改";
    const next = {...old, layout_version: 5,
        nodes: [item("s1", [500, 80], 5), item("s2", [500, 800], 5)]};
    const mapping = mountGraph(board, next, true);
    assert.deepEqual(Array.from(mapping.get("s1").pos), [500, 80]);
    assert.deepEqual(Array.from(mapping.get("s2").pos), [500, 800]);
    assert.equal(first.widgets.find(w => w.name === "prompt").value, "人工修改");
    assert.equal(first.properties.astrogate_images[0].filename, "s1.png");
});
test("imported video frames connect as image references in the existing board", async () => {
    const {graph, LiteGraph} = graphMock(); graph.change = () => {};
    const board = LiteGraph.createNode("AstroGateVideoAnalysis"); board.pos = [0, 0]; graph.add(board);
    const app = {registerExtension() {}, graph, nodeOutputs: {}};
    const {mountGraph} = await load(app, {LiteGraph});
    const recipe = {project_id: "p", chapter_id: "c", layout_version: 5, reference_wiring_version: 4,
        nodes: [{id: "shot", type: "AstroGateStoryboardFrame", title: "近景", pos: [500, 80],
            widgets: {prompt: "近景", job_id: "", aspect_ratio: "16:9", resolution: "4K", quality: "high"},
            properties: {astrogate_project: "p", astrogate_owner: "shot", astrogate_key: "shot", astrogate_kind: "shot", astrogate_base_prompt: "近景"}},
            {id: "shot::video_source", type: "LoadImage", title: "原片", pos: [250, 80], widgets: {image: "astrogate/p/frame.png"},
                properties: {astrogate_project: "p", astrogate_key: "shot::video_source", astrogate_kind: "video_source"}}],
        links: [{from: "$board", slot: 0, to: "shot", input: "board", type: "ASTROGATE_BOARD"},
            {from: "shot::video_source", slot: 0, to: "shot", input: "reference_images.reference_1", type: "IMAGE"}]};
    const mapping = mountGraph(board, recipe);
    assert.equal(mapping.get("shot::video_source").type, "LoadImage");
    assert.equal(mapping.get("shot").inputs.find(i => i.name === "reference_images.reference_1").link != null, true);
    assert.equal(mapping.get("shot::video_source").widgets.find(w => w.name === "image").value, "astrogate/p/frame.png");
});
test("legacy folding preserves values, references and output previews", async () => {
    const app = {registerExtension() {}, nodeOutputs: {}};
    const {installFold} = await load(app);
    class Node {
        constructor() {
            this.id = 12; this.properties = {}; this.flags = {};
            this.widgets = [{name: "prompt", value: "两个人"}, {name: "consistency", value: "蓝衣与黑衣"}];
            this.inputs = [{name: "reference_images.reference_1", link: 77}];
        }
        addWidget(type, name, value, callback, options) { const w = {type, name, value, callback, options}; this.widgets.push(w); return w; }
        setDirtyCanvas() {}
    }
    installFold(Node);
    const node = new Node(); node.onNodeCreated(); node.widgets.at(-1).callback();
    assert.equal(node.widgets[0].hidden, true);
    assert.equal(node.widgets[0].value, "两个人");
    assert.equal(node.inputs[0].link, 77);
    assert.equal(node.flags.collapsed, undefined);
    node.onExecuted({images: [{filename: "frame.png", type: "output", subfolder: "AstroGate"}]});
    node.onConfigure();
    await new Promise(resolve => queueMicrotask(resolve));
    assert.equal(app.nodeOutputs[12].images[0].filename, "frame.png");
    node.widgets.at(-1).callback(); assert.equal(node.widgets[0].hidden, false);
});
test("unconfirmed scene skips its image without submitting a request", async () => {
    const {graph, LiteGraph} = graphMock(); graph.change = () => {};
    const node = LiteGraph.createNode("AstroGateStoryboardFrame"); graph.add(node);
    node.title = "待确认场景";
    node.properties = {astrogate_project: "p", astrogate_kind: "shot", astrogate_unresolved_scene: true};
    const calls = [];
    const app = {registerExtension() {}, graph, nodeOutputs: {}};
    const {generate} = await load(app, {FormData: class {}}, {fetchApi: async (...args) => { calls.push(args); throw new Error("unexpected request"); }});
    await generate([node]);
    assert.match(node.properties.astrogate_status, /场景引用待确认/);
    assert.equal(calls.length, 0);
});
test("unconfirmed person queues a text-only image and keeps the review marker", async () => {
    const {graph, LiteGraph} = graphMock(); graph.change = () => {};
    const node = LiteGraph.createNode("AstroGateStoryboardFrame"); graph.add(node);
    node.title = "待确认人物";
    node.properties = {astrogate_project: "p", astrogate_kind: "shot", astrogate_unresolved_people: true};
    const calls = [];
    const app = {registerExtension() {}, graph, nodeOutputs: {}};
    const {generate} = await load(app, {FormData: class {}}, {fetchApi: async (path, options) => {
        calls.push([path, options?.body ? JSON.parse(options.body) : null]);
        return {ok: true, json: async () => path.endsWith("/native/prepare") ? {id: "text-only", status: "prepared", references: []} : {}};
    }});
    await generate([node]);
    assert.deepEqual(calls.find(([path]) => path.endsWith("/native/prepare"))[1].references, []);
    assert.equal(calls.filter(([path]) => path.endsWith("/jobs/text-only/start")).length, 1);
    assert.equal(node.properties.astrogate_unresolved_people, true);
});
test("dialogue cards preserve shared edits and preview Unicode character ranges", async () => {
    const elements = [];
    class Element {
        constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.classList = {toggle() {}}; elements.push(this); }
        append(...items) { this.children.push(...items); }
        replaceChildren(...items) { this.children = items; }
        addEventListener(name, fn) { (this.events[name] ||= []).push(fn); }
        fire(name) { for (const fn of this.events[name] || []) fn({}); }
    }
    const {graph, LiteGraph} = graphMock(); graph.change = () => {};
    const first = LiteGraph.createNode("AstroGateStoryboardFrame"), second = LiteGraph.createNode("AstroGateStoryboardFrame");
    graph.add(first); graph.add(second);
    for (const node of [first, second]) {
        node.properties = {astrogate_project: "project", astrogate_owner: node === first ? "s1" : "s2", astrogate_kind: "shot"};
        node.agStatus = new Element("div");
    }
    let project = {id:"project", revision:1, dialogue_revision:1, assets:[], dialogues:[{id:"d", chapter_id:"c", text:"你😀好", type:"dialogue", source:"original", status:"pending", revision:1}],
        shots:[{id:"s1", chapter_id:"c", dialogue_links:[{dialogue_id:"d", start:1, end:2, mode:"start", show_subtitle:true}]},
            {id:"s2", chapter_id:"c", dialogue_links:[{dialogue_id:"d", start:2, end:3, mode:"finish", show_subtitle:true}]}]};
    const calls = [];
    const api = {fetchApi: async (path, options) => {
        const body = options.body && JSON.parse(options.body); calls.push(body);
        if (!body) return {ok:true, json:async()=>({project})};
        project = {...project, revision:project.revision + 1, dialogue_revision:project.dialogue_revision + 1,
            dialogues:project.dialogues.map(item => item.id === body.dialogue?.id ? {...item, ...body.dialogue, revision:item.revision + 1} : item)};
        return {ok:true, json:async()=>project};
    }};
    const {installDialogue} = await load({registerExtension(){}, graph}, {FormData:class {}, document:{createElement:tag=>new Element(tag)}}, api);
    const card1 = installDialogue(first, () => {}), card2 = installDialogue(second, () => {});
    first.agLoadDialogue(); second.agLoadDialogue();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.match(card1.children[0].textContent, /台词 1/);
    assert.equal(card1.open, false);
    const row1 = card1.children[1].children[0], row2 = card2.children[1].children[0];
    assert.match(row1.children.find(el=>el.className === "ag-dialogue-preview").textContent, /😀/);
    const text1 = row1.children.find(el=>el.tag === "textarea"), text2 = row2.children.find(el=>el.tag === "textarea");
    text2.value = "未保存的另一镜编辑"; text2.fire("input");
    text1.value = "你好"; text1.fire("input");
    row1.children.find(el=>el.textContent === "保存共享台词").fire("click");
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(calls.at(-1).dialogue.revision, 1);
    assert.equal(text2.value, "未保存的另一镜编辑");
    assert.equal(card2.children[1].children[0], row2);
});
test("native board creates real image/story wires, shares assets and preserves manual edits on restore", async () => {
    const {graph, LiteGraph} = graphMock();
    const board = LiteGraph.createNode("AstroGateStoryboard"); board.pos = [100, 100]; graph.add(board);
    const {mountGraph, changed} = await load({registerExtension() {}, graph, nodeOutputs: {}}, {LiteGraph});
    const recipe = {project_id: "p", chapter_id: "c", nodes: ["asset", "shot1", "shot2"].map((id, i) => ({id, type: "AstroGateStoryboardFrame", title: id, pos: [i * 500, 100], widgets: {prompt: id, job_id: "j-" + id, aspect_ratio: "16:9", resolution: "4K", quality: "high"}, properties: {astrogate_project: "p", astrogate_key: id, astrogate_owner: id, astrogate_base_prompt: id}})), links: [
        {from: "$board", slot: 0, to: "shot1", input: "board", type: "ASTROGATE_BOARD"},
        {from: "asset", slot: 0, to: "shot1", input: "reference_images.reference_1", type: "IMAGE"},
        {from: "asset", slot: 0, to: "shot2", input: "reference_images.reference_1", type: "IMAGE"},
        {from: "shot1", slot: 1, to: "shot2", input: "story_context", type: "ASTROGATE_STORY"},
    ]};
    const map = mountGraph(board, recipe);
    const first = map.get("shot1"), second = map.get("shot2"), asset = map.get("asset");
    const getLink = (n, name) => graph.links[n.inputs.find(i => i.name === name).link];
    assert.equal(getLink(first, "reference_images.reference_1").origin_id, getLink(second, "reference_images.reference_1").origin_id);
    assert.equal(getLink(second, "story_context").origin_slot, 1);
    assert.equal(changed(second), false);
    asset.widgets[0].value = "修改身份";
    assert.equal(changed(first), false);
    assert.equal(changed(second), false);
    asset.widgets[1].value = "regenerated-asset";
    assert.equal(changed(first), true);
    assert.equal(changed(second), true);
    first.widgets[0].value = "手动改词"; first.pos = [900, 420];
    first.inputs.find(i => i.name === "reference_images.reference_1").link = null;
    mountGraph(board, recipe);
    assert.equal(graph._nodes.length, 4);
    assert.equal(first.widgets[0].value, "手动改词");
    assert.deepEqual(first.pos, [900, 420]);
    assert.equal(first.inputs.find(i => i.name === "reference_images.reference_1").link, null);
});
test("scene state default gains a new viewpoint without overwriting manual prompts", async () => {
    const {graph, LiteGraph} = graphMock();
    const board = LiteGraph.createNode("AstroGateStoryboard"); board.pos = [0, 0]; graph.add(board);
    const {mountGraph, changed} = await load({registerExtension() {}, graph, nodeOutputs: {}}, {LiteGraph});
    const recipe = {project_id: "p", chapter_id: "c", nodes: [{id: "state", type: "AstroGateStoryboardFrame", title: "清晨客栈",
        pos: [500, 0], default_prompt: "清晨客栈，侧方机位", widgets: {prompt: "清晨客栈，正面机位", job_id: "saved-job"},
        properties: {astrogate_project: "p", astrogate_key: "state", astrogate_kind: "scene_state", astrogate_base_prompt: "清晨客栈，正面机位"}}], links: []};
    const card = mountGraph(board, recipe).get("state");
    assert.equal(card.widgets[0].value, "清晨客栈，侧方机位");
    assert.equal(card.widgets[1].value, "saved-job");
    assert.equal(changed(card), false);
    card.widgets[0].value = "用户自定机位";
    mountGraph(board, recipe);
    assert.equal(card.widgets[0].value, "用户自定机位");
});
test("existing shots drop redundant character views only when their look remains connected", async () => {
    const {graph, LiteGraph} = graphMock();
    graph.change = () => {};
    const board = LiteGraph.createNode("AstroGateStoryboard"); board.pos = [0, 0]; graph.add(board);
    const {mountGraph, changed} = await load({registerExtension() {}, graph, nodeOutputs: {}}, {LiteGraph});
    const items = [
        ["front", "character", "person-a"], ["left", "character", "person-a"],
        ["look", "look", "look-a"], ["other", "character", "person-b"], ["shot", "shot", "shot"],
    ].map(([id,kind,owner],i) => ({id,type:"AstroGateStoryboardFrame",title:id,pos:[i*500,100],
        widgets:{prompt:id,job_id:"job-"+id,aspect_ratio:"16:9",resolution:"4K",quality:"high"},
        properties:{astrogate_project:"p",astrogate_key:id,astrogate_kind:kind,astrogate_owner:owner,
            astrogate_base_prompt:id,astrogate_images:[{filename:id+".png"}]}}));
    const wire = (from,to,input) => ({from,to,input,type:"IMAGE",slot:0});
    const old = {project_id:"p",chapter_id:"c",nodes:items,links:[wire("front","look","reference_images.reference_1"),
        wire("front","shot","reference_images.reference_1"),wire("left","shot","reference_images.reference_2"),
        wire("look","shot","reference_images.reference_3"),wire("other","shot","reference_images.reference_4")]};
    const mapping = mountGraph(board,old);
    const shot = mapping.get("shot"), look = mapping.get("look");
    const next = {...old,reference_wiring_version:2,links:[wire("front","look","reference_images.reference_1"),
        wire("look","shot","reference_images.reference_1"),wire("other","shot","reference_images.reference_2")]};
    assert.equal(changed(shot),false);
    mountGraph(board,next);
    assert.deepEqual(shot.inputs.map(i=>i.link == null ? null : graph.links[i.link].origin_id),[null,null,look.id,mapping.get("other").id]);
    assert.equal(shot.properties.astrogate_images[0].filename,"shot.png");
    assert.equal(changed(shot),false);
    assert.equal(board.properties.astrogate_wiring_version,2);
    assert.notEqual(look.inputs[0].link,null);
});
test("wiring migration preserves deliberately assigned reference roles", async () => {
    const {graph, LiteGraph} = graphMock();
    graph.change = () => {};
    const board = LiteGraph.createNode("AstroGateStoryboard"); board.pos = [0,0]; graph.add(board);
    const {mountGraph} = await load({registerExtension(){},graph,nodeOutputs:{}},{LiteGraph});
    const nodes = [["front","character","person"],["look","look","look"],["shot","shot","shot"]].map(([id,kind,owner],i)=>({
        id,type:"AstroGateStoryboardFrame",title:id,pos:[i*500,100],widgets:{prompt:id,job_id:"",aspect_ratio:"16:9",resolution:"4K",quality:"high"},
        properties:{astrogate_project:"p",astrogate_key:id,astrogate_kind:kind,astrogate_owner:owner,astrogate_base_prompt:id}}));
    const wire = (from,to,input)=>({from,to,input,type:"IMAGE",slot:0});
    const old = {project_id:"p",chapter_id:"c",nodes,links:[wire("front","look","reference_images.reference_1"),
        wire("front","shot","reference_images.reference_1"),wire("look","shot","reference_images.reference_2")]};
    const mapping=mountGraph(board,old), shot=mapping.get("shot"), front=mapping.get("front");
    shot.properties.astrogate_reference_roles={"reference_images.reference_1":{source:String(front.id),role:"style",detail:"保留构图"}};
    mountGraph(board,{...old,reference_wiring_version:2,links:[wire("front","look","reference_images.reference_1"),wire("look","shot","reference_images.reference_1")]});
    assert.equal(graph.links[shot.inputs[0].link].origin_id,front.id);
    assert.equal(shot.properties.astrogate_reference_roles["reference_images.reference_1"].detail,"保留构图");
});
test("story edges never prepare or regenerate the preceding image", async () => {
    const {graph, LiteGraph} = graphMock();
    const make = (name, job) => { const n = LiteGraph.createNode("AstroGateStoryboardFrame"); graph.add(n); n.properties = {astrogate_project: "p", astrogate_owner: name, astrogate_owner_type: "shot", astrogate_base_prompt: name}; n.widgets[0].value = name; n.widgets[1].value = job; return n; };
    const previous = make("previous", "unknown-old"), target = make("target", "");
    target.addInput("story_context", "ASTROGATE_STORY"); previous.connect(1, target, 0);
    const calls = [];
    const api = {fetchApi: async (path, options) => {
        calls.push([path, options?.body]);
        return {ok: true, json: async () => path.endsWith("/native/prepare") ? {id: "new", status: "prepared"} : {}};
    }};
    const app = {registerExtension() {}, graph, nodeOutputs: {}, graphToPrompt: async () => ({output: {}, workflow: {}})};
    const {generate} = await load(app, {LiteGraph, FormData: class {}, crypto: {randomUUID: () => "prompt-id"}}, api);
    await generate([target]);
    assert.equal(calls.filter(([p]) => p.endsWith("/native/prepare")).length, 1);
    assert.equal(calls.some(([p]) => p.includes("unknown-old")), false);
    const prepared = JSON.parse(calls.find(([p]) => p.endsWith("/native/prepare"))[1]);
    assert.equal(prepared.story_parent_id, "previous");
    assert.deepEqual(prepared.references, []);
    assert.equal(calls.filter(([p]) => p.endsWith("/jobs/new/start")).length, 1);
});

test("regenerating a later frame reuses saved predecessors even when their controls were edited", async () => {
    const {graph, LiteGraph} = graphMock();
    const make = (name, job) => {
        const node = LiteGraph.createNode("AstroGateStoryboardFrame"); graph.add(node);
        node.title = name; node.widgets[0].value = name; node.widgets[1].value = job;
        node.properties = {astrogate_project: "p", astrogate_owner: name, astrogate_base_prompt: name,
            astrogate_images: [{filename: name + ".png"}]};
        return node;
    };
    const missing = make("old-source", "unknown-job"), previous = make("previous", "previous-job");
    const target = make("target", "target-job"), later = make("later", "later-job");
    previous.addInput("reference_images.reference_1", "IMAGE"); missing.connect(0, previous, 0);
    target.addInput("reference_images.reference_1", "IMAGE"); previous.connect(0, target, 0);
    later.addInput("reference_images.reference_1", "IMAGE"); target.connect(0, later, 0);
    previous.widgets[0].value = "修改前图提示词，但还没有重绘";
    const calls = [];
    const api = {fetchApi: async (path, options) => {
        const body = options?.body ? JSON.parse(options.body) : null; calls.push([path, body]);
        const result = path.endsWith("/native/prepare") ? {id: "new-target", status: "prepared"} :
            path.includes("/jobs/") ? {id: path.split("/").at(-1), status: "succeeded"} : {};
        return {ok: true, json: async () => result};
    }};
    const app = {registerExtension() {}, graph, nodeOutputs: {}, graphToPrompt: async () => ({output: {}, workflow: {}})};
    const {generate} = await load(app, {FormData: class {}, crypto: {randomUUID: () => "batch-id"}}, api);
    await generate([target], true);
    const prepared = calls.filter(([path]) => path.endsWith("/native/prepare")).map(([, body]) => body);
    assert.deepEqual(prepared.map(body => body.owner_id), ["target"]);
    assert.equal(prepared[0].references[0].job_id, "previous-job");
    assert.equal(calls.some(([path]) => path.endsWith("/jobs/unknown-job")), false);
    assert.equal(previous.widgets[1].value, "previous-job");
    assert.equal(later.widgets[1].value, "later-job");
    assert.equal(later.properties.astrogate_images[0].filename, "later.png");
});

for (const force of [false, true]) test(`selected predecessors update before selected later frames regardless of selection order (force=${force})`, async () => {
    const {graph, LiteGraph} = graphMock();
    const make = name => {
        const node = LiteGraph.createNode("AstroGateStoryboardFrame"); graph.add(node);
        node.widgets[0].value = name; node.widgets[1].value = name + "-old";
        node.properties = {astrogate_project: "p", astrogate_owner: name, astrogate_base_prompt: name};
        return node;
    };
    const previous = make("previous"), target = make("target");
    target.addInput("reference_images.reference_1", "IMAGE"); previous.connect(0, target, 0);
    const prepared = [];
    const api = {fetchApi: async (path, options) => {
        const body = options?.body ? JSON.parse(options.body) : null;
        if (path.endsWith("/native/prepare")) prepared.push(body);
        const result = path.endsWith("/native/prepare") ? {id: body.owner_id + "-new", status: "prepared"} :
            path.includes("/jobs/") ? {status: "succeeded"} : {};
        return {ok: true, json: async () => result};
    }};
    const app = {registerExtension() {}, graph, nodeOutputs: {}, graphToPrompt: async () => ({output: {}, workflow: {}})};
    const {generate, nodeSignature} = await load(app, {FormData: class {}, crypto: {randomUUID: () => "batch-id"}}, api);
    target.properties.astrogate_signature = nodeSignature(target);
    previous.widgets[0].value = "修改前图";
    await generate([target, previous], force);
    assert.deepEqual(prepared.map(body => body.owner_id), ["previous", "target"]);
    assert.equal(prepared[1].references[0].job_id, "previous-new");
    assert.equal(prepared.every(body => body.force === force), true);
});

test('batch skips unknown and running branches but still queues independent targets',async()=>{
    const {graph,LiteGraph}=graphMock();
    const make=(name,job='')=>{
        const node=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(node);
        node.title=name;node.widgets[0].value=name;node.widgets[1].value=job;
        node.properties={astrogate_project:'p',astrogate_owner:name,astrogate_owner_type:'shot',astrogate_base_prompt:name};
        node.agStatus={textContent:'',classList:{toggle(){}}};return node;
    };
    const unknown=make('未知人物','unknown-job'), running=make('生成中的场景','running-job');
    const blocked=make('依赖未知人物'), busy=make('依赖场景'), independent=make('独立镜头');
    blocked.addInput('reference_images.reference_1','IMAGE');unknown.connect(0,blocked,0);
    busy.addInput('reference_images.reference_1','IMAGE');running.connect(0,busy,0);
    const calls=[];
    const api={fetchApi:async(path,options)=>{
        const body=options?.body?JSON.parse(options.body):null;calls.push([path,body]);
        const result=path.endsWith('/jobs/unknown-job')?{id:'unknown-job',status:'unknown'}:
            path.endsWith('/jobs/running-job')?{id:'running-job',status:'running'}:
            path.endsWith('/native/prepare')?{id:'independent-job',status:'prepared'}:{};
        return {ok:true,json:async()=>result};
    }};
    const app={registerExtension(){},graph,nodeOutputs:{},graphToPrompt:async()=>({output:{},workflow:{}})};
    const {generate}=await load(app,{LiteGraph,FormData:class{},crypto:{randomUUID:()=> 'batch-id'}},api);
    await generate([blocked,busy,independent]);
    assert.match(blocked.agStatus.textContent,/未知人物.*结果未知.*重绘/);
    assert.match(busy.agStatus.textContent,/生成中的场景.*任务正在进行/);
    assert.deepEqual(calls.filter(([path])=>path.endsWith('/native/prepare')).map(([,body])=>body.owner_id),['独立镜头']);
    assert.equal(calls.filter(([path])=>path.endsWith('/jobs/independent-job/start')).length,1);
    assert.equal(unknown.widgets[1].value,'unknown-job');
    assert.equal(running.widgets[1].value,'running-job');
});

test('explicit batch retry queues an unknown source even when only its dependent frame is selected',async()=>{
    const {graph,LiteGraph}=graphMock();
    const source=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(source);
    source.title='未知人物';source.widgets[0].value='人物';source.widgets[1].value='unknown-job';
    source.properties={astrogate_project:'p',astrogate_owner:'actor',astrogate_owner_type:'asset',astrogate_base_prompt:'人物'};
    source.agStatus={textContent:'',classList:{toggle(){}}};
    const target=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(target);
    target.title='分镜';target.widgets[0].value='分镜';
    target.properties={astrogate_project:'p',astrogate_owner:'shot',astrogate_owner_type:'shot',astrogate_base_prompt:'分镜'};
    target.agStatus={textContent:'',classList:{toggle(){}}};
    target.addInput('reference_images.reference_1','IMAGE');source.connect(0,target,0);
    const calls=[];
    const api={fetchApi:async(path,options)=>{
        const body=options?.body?JSON.parse(options.body):null;calls.push([path,body]);
        const result=path.endsWith('/jobs/unknown-job')?{id:'unknown-job',status:'unknown'}:
            path.endsWith('/native/prepare')?{id:'redrawn-'+body.owner_id,status:'prepared',references:body.references}:{};
        return {ok:true,json:async()=>result};
    }};
    const app={registerExtension(){},graph,nodeOutputs:{},graphToPrompt:async()=>({output:{},workflow:{}})};
    const {generate}=await load(app,{LiteGraph,FormData:class{},crypto:{randomUUID:()=> 'batch-id'}},api);
    await generate([target],true);
    const prepared=calls.filter(([path])=>path.endsWith('/native/prepare')).map(([,body])=>body);
    assert.deepEqual(prepared.map(body=>body.owner_id),['actor','shot']);
    assert(prepared.every(body=>body.force));
    assert.equal(prepared[1].references[0].job_id,'redrawn-actor');
    assert.equal(calls.filter(([path])=>path.endsWith('/start')).length,2);
});

test('a skipped branch keeps its reason while the source is unresolved and clears it when resolved',async()=>{
    const {graph,LiteGraph}=graphMock();
    const source=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(source);
    source.title='人物';source.widgets[0].value='人物';source.widgets[1].value='source-job';
    source.properties={astrogate_project:'p',astrogate_owner:'actor',astrogate_base_prompt:'人物'};
    source.agStatus={textContent:'',classList:{toggle(){}}};
    const target=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(target);
    target.title='分镜';target.widgets[0].value='分镜';target.widgets[1].value='target-job';
    target.properties={astrogate_project:'p',astrogate_owner:'shot',astrogate_base_prompt:'分镜'};
    target.agStatus={textContent:'',classList:{toggle(){}}};
    target.addInput('reference_images.reference_1','IMAGE');source.connect(0,target,0);
    let sourceStatus='unknown';
    const api={fetchApi:async(path)=>({ok:true,json:async()=>{
        if(path.endsWith('/jobs/source-job'))return {id:'source-job',status:sourceStatus};
        if(path.endsWith('/jobs/target-job'))return {id:'target-job',status:'blocked'};
        if(path.endsWith('?summary=1'))return {jobs:[{id:'source-job',kind:'image',status:sourceStatus},{id:'target-job',kind:'image',status:'blocked'}]};
        return {};
    }})};
    const app={registerExtension(){},graph,nodeOutputs:{}};
    const {generate,poll}=await load(app,{FormData:class{}},api);
    await generate([target]);
    await poll();
    assert.match(target.agStatus.textContent,/人物.*结果未知.*重绘/);
    sourceStatus='succeeded';
    await poll();
    assert.equal(target.agSkipped,undefined);
    assert.match(target.agStatus.textContent,/本批次未执行/);
});

test('poll replaces a stale failed card with its newer completed image job',async()=>{
    const {graph,LiteGraph}=graphMock();
    const node=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(node);
    node.widgets[0].value='分镜';node.widgets[1].value='old-job';
    node.properties={astrogate_project:'p',astrogate_owner:'shot',astrogate_owner_type:'shot',astrogate_base_prompt:'分镜'};
    node.agStatus={textContent:'',classList:{toggle(){}}};
    const api={fetchApi:async()=>({ok:true,json:async()=>({jobs:[
        {id:'new-job',kind:'image',native:true,owner_type:'shot',owner_id:'shot',asset_view:'',created_at:2,status:'succeeded'},
        {id:'old-job',kind:'image',native:true,owner_type:'shot',owner_id:'shot',asset_view:'',created_at:1,status:'unknown'},
    ]})})};
    const app={registerExtension(){},graph,nodeOutputs:{}};
    const {poll}=await load(app,{FormData:class{}},api);
    await poll();
    assert.equal(node.widgets[1].value,'new-job');
    assert.match(node.agStatus.textContent,/已完成/);
});

test('poll restores an externally completed image to a card without a job id',async()=>{
    const {graph,LiteGraph}=graphMock();
    const node=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(node);
    node.widgets[0].value='分镜';
    node.properties={astrogate_project:'p',astrogate_owner:'shot',astrogate_owner_type:'shot',astrogate_base_prompt:'分镜',astrogate_signature:'stale'};
    node.agStatus={textContent:'',classList:{toggle(){}}};
    const calls=[];
    const api={fetchApi:async(path)=>{calls.push(path);return {ok:true,json:async()=>({jobs:[
        {id:'unknown-job',kind:'image',native:true,owner_type:'shot',owner_id:'shot',created_at:2,status:'unknown'},
        {id:'saved-job',kind:'image',native:true,owner_type:'shot',owner_id:'shot',created_at:1,status:'succeeded',
            saved_result:{filename:'shot.png',subfolder:'AstroGate',type:'output'}},
    ]})};}};
    const app={registerExtension(){},graph,nodeOutputs:{}};
    const {poll}=await load(app,{FormData:class{}},api);
    await poll();
    assert.equal(node.widgets[1].value,'saved-job');
    assert.equal(node.properties.astrogate_images[0].filename,'shot.png');
    assert.match(node.agStatus.textContent,/已完成/);
    assert.equal(calls.some(path=>path.endsWith('/native/prepare')),false);
});

test('poll refreshes unresolved card state when the project changes outside the canvas',async()=>{
    const {graph,LiteGraph}=graphMock();
    const node=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(node);
    node.properties={astrogate_project:'p',astrogate_owner:'shot',astrogate_kind:'shot',astrogate_unresolved_scene:true};
    node.agRenderScene=project=>{node.properties.astrogate_unresolved_scene=project.unresolved;};
    let revision=1;
    const api={fetchApi:async(path)=>({ok:true,json:async()=>path.endsWith('?summary=1')?
        {revision,jobs:[]}:{project:{id:'p',unresolved:revision===1}}})};
    const {poll}=await load({registerExtension(){},graph,nodeOutputs:{}},{FormData:class{}},api);
    await poll();
    assert.equal(node.properties.astrogate_unresolved_scene,true);
    revision=2;
    await poll();
    assert.equal(node.properties.astrogate_unresolved_scene,false);
});

test('unknown image recovery queries without regeneration and only shows a saved result as complete',async()=>{
    const elements=[];
    class Element {
        constructor(tag){this.tag=tag;this.children=[];this.events={};this.classList={toggle(){}};elements.push(this);}
        append(...items){this.children.push(...items);}
        replaceChildren(...items){this.children=items;}
        addEventListener(name,fn){this.events[name]=fn;}
        getAttribute(name){return this[name];}
        removeAttribute(name){delete this[name];}
    }
    class ResizeObserver {observe(){} disconnect(){}}
    const {graph,LiteGraph}=graphMock();graph.change=()=>{};
    const node=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(node);
    node.title='未知图片';node.properties={astrogate_project:'p',astrogate_kind:'shot'};
    node.widgets[1].value='unknown-job';node.widgets.forEach(w=>{w.options={values:['16:9','4K','high']};});
    node.addDOMWidget=()=>({options:{}});node.setSize=size=>node.size=size;node.computeSize=()=>[430,440];
    const calls=[];
    let status='pending';
    const api={apiURL:path=>path,fetchApi:async(path,options)=>{
        calls.push([path,options?.method]);
        const result=path.endsWith('/recover')?{status,...(status==='succeeded'?{saved_result:{filename:'saved.png',type:'output',subfolder:'AstroGate'}}:{})}:
            path.endsWith('?summary=1')?{jobs:[{id:'unknown-job',kind:'image',status:'unknown'}]}:{};
        return {ok:true,json:async()=>result};
    }};
    const app={registerExtension(){},graph,nodeOutputs:{}};
    const {installFrame,poll}=await load(app,{ResizeObserver,document:{createElement:tag=>new Element(tag)},FormData:class{}},api);
    installFrame(node);
    const recover=elements.find(e=>e.textContent==='查询并取回');
    assert.equal(recover.hidden,true);
    await poll();assert.equal(recover.hidden,false);
    await recover.events.click();
    assert.match(node.agProgress.label.textContent,/尚未确认结果.*不会重复提交/);
    await poll();assert.match(node.agProgress.label.textContent,/尚未确认结果/);
    status='unknown';await recover.events.click();
    assert.match(node.agStatus.textContent,/请求已异常中断.*未重新生成.*重绘/);
    await poll();assert.match(node.agProgress.label.textContent,/请求已异常中断/);
    status='missing';await recover.events.click();
    assert.match(node.agStatus.textContent,/未找到结果.*重绘/);
    status='expired';await recover.events.click();
    assert.match(node.agStatus.textContent,/已过期.*重绘/);
    status='unavailable';await recover.events.click();
    assert.match(node.agStatus.textContent,/暂不可用.*稍后再查询/);
    status='succeeded';await recover.events.click();
    assert.equal(node.properties.astrogate_images[0].filename,'saved.png');
    assert.equal(node.agImage.hidden,false);assert.equal(recover.hidden,true);
    assert.equal(node.agProgress.bar.value,1);
    assert.equal(calls.filter(([path])=>path.endsWith('/recover')).length,6);
    assert.equal(calls.some(([path])=>path==='/prompt'||path.endsWith('/native/prepare')),false);
});

function analysisGraph() {
    const {graph, LiteGraph} = graphMock();
    const file = LiteGraph.createNode('AstroGateTXT');
    file.widgets = [{name:'project_id', value:'project'}];
    graph.add(file);
    const board = LiteGraph.createNode('AstroGateStoryboard');
    board.widgets = Object.entries({text_model:'gpt-5.6-terra', chapter:1, style:'电影光线，雨夜\n第二行', density:'精细拆镜 😀', analysis_revision:0}).map(([name,value])=>({name,value}));
    board.widgets.push({name:'$$node-text-preview',value:'AI 正在分析剧本…'});
    board.agAnalysisButton = {};
    board.agRetryButton = {};
    board.agStatus = {textContent:'',classList:{toggle(){}}};
    board.pos=[0,0];
    graph.add(board);
    board.addInput('script','ASTROGATE_SCRIPT');
    file.connect(0,board,0);
    graph.change=()=>{};
    graph.serialize=()=>({});
    return {graph,LiteGraph,board};
}
const expectedAnalysisKey='25d11e2341c4c999ced946d4f9341486401e3c48b8049de8eebfeba8aa870026';
const failedAnalysis={id:'failed-job',kind:'text',status:'failed',error:'文本接口 HTTP 503，未自动重试或切换模型。',analysis_key:expectedAnalysisKey,chapter_id:'chapter-id'};
function analysisAPI(calls, jobs) {
    return {fetchApi:async(path,options)=>{
        calls.push([path,options?.body]);
        let data={};
        if (path.endsWith('/projects/project')) data={project:{source:{hash:'source-hash'},chapters:[{id:'chapter-id'}]}};
        if (path.endsWith('?summary=1')) data={jobs};
        if (path.endsWith('/native/canvas')) data={project_id:'project',chapter_id:'chapter-id',nodes:[],links:[]};
        return {ok:true,json:async()=>data};
    }};
}
test('analysis key matches Python digest with Chinese text, newline and emoji',async()=>{
    const {graph,board}=analysisGraph();
    const {analysisContext}=await load({registerExtension(){},graph},{FormData:class{}},analysisAPI([],[]));
    const context=await analysisContext(board);
    assert.equal(context.key,expectedAnalysisKey);
    assert.equal(context.chapterId,'chapter-id');
});
test('failed analysis reveals original reason and hides stale progress without submitting again',async()=>{
    const {graph,board}=analysisGraph();
    const calls=[];
    const {startAnalysis}=await load({registerExtension(){},graph},{FormData:class{}},analysisAPI(calls,[failedAnalysis]));
    await startAnalysis(board);
    assert.match(board.agStatus.textContent,/HTTP 503/);
    assert.equal(board.agRetryButton.hidden,false);
    assert.equal(board.widgets.at(-1).hidden,true);
    assert.equal(calls.some(([p])=>p==='/prompt'||p.endsWith('/resume')),false);
});
test('explicit retry resumes the failed job and keeps completed stage checkpoints',async()=>{
    const {graph,board}=analysisGraph();
    const calls=[];
    const {startAnalysis}=await load({registerExtension(){},graph},{FormData:class{}},analysisAPI(calls,[failedAnalysis]));
    await startAnalysis(board,true);
    assert.equal(calls.filter(([p])=>p.endsWith('/jobs/failed-job/resume')).length,1);
    assert.equal(calls.some(([p])=>p==='/prompt'),false);
    assert.equal(board.widgets.find(w=>w.name==='analysis_revision').value,0);
    assert.equal(board.agAnalysisButton.disabled,true);
});
test('unknown results require an explicit retry and are labelled before submission',async()=>{
    const {graph,board}=analysisGraph();
    const calls=[];
    const job={...failedAnalysis,status:'unknown',error:'远端结果未知'};
    const {startAnalysis}=await load({registerExtension(){},graph},{FormData:class{}},analysisAPI(calls,[job]));
    await startAnalysis(board);
    assert.equal(board.agRetryButton.textContent,'结果未知，重新提交');
    assert.equal(calls.some(([p])=>p.endsWith('/resume')),false);
});

test('analysis progress shows the current phase and elapsed waiting time',async()=>{
    const {graph,board}=analysisGraph();
    const {showAnalysisState}=await load({registerExtension(){},graph},{Date:{now:()=>120000}});
    showAnalysisState(board,{status:'analysing',stage:'shots',stage_started_at:60,completed_chunks:0,total_chunks:1});
    assert.match(board.agStatus.textContent,/正在编写分镜与提示词/);
    assert.match(board.agStatus.textContent,/已等待 60 秒/);
    assert.equal(board.agAnalysisButton.disabled,true);
    assert.equal(board.agRetryButton.hidden,true);
});
test('completed resumed analysis automatically builds native nodes without another API analysis',async()=>{
    const {graph,LiteGraph,board}=analysisGraph();
    const calls=[];
    const {poll}=await load({registerExtension(){},graph,nodeOutputs:{}},{LiteGraph,FormData:class{}},analysisAPI(calls,[{...failedAnalysis,status:'succeeded',error:null}]));
    await poll();
    assert.equal(board.properties.astrogate_board.analysis_id,'failed-job');
    assert.equal(calls.filter(([p])=>p.endsWith('/native/canvas')).length,1);
    assert.equal(calls.some(([p])=>p.endsWith('/resume')||p==='/prompt'),false);
    await poll();
    assert.equal(calls.filter(([p])=>p.endsWith('/native/canvas')).length,1);
});

test('poll queues previously skipped shots once',async()=>{
    const {graph,LiteGraph,board}=analysisGraph();
    graph.serialize=()=>({});
    board.properties.astrogate_board={project_id:'project',chapter_id:'chapter-id',analysis_id:'analysis'};
    board.properties.astrogate_auto_started='analysis';
    const shot=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(shot);
    shot.title='未提交镜头';shot.widgets[0].value='人物站在门口';
    shot.properties={astrogate_project:'project',astrogate_chapter:'chapter-id',astrogate_owner:'shot',
        astrogate_owner_type:'shot',astrogate_kind:'shot',astrogate_base_prompt:'人物站在门口'};
    shot.addInput('board','ASTROGATE_BOARD');board.connect(0,shot,0);
    const calls=[];
    const api={fetchApi:async(path,options)=>{
        calls.push([path,options?.body?JSON.parse(options.body):null]);
        const data=path.endsWith('?summary=1')?{jobs:[]}:
            path.endsWith('/projects/project')?{project:{source:{hash:'source-hash'},chapters:[{id:'chapter-id'}]}}:
            path.endsWith('/native/prepare')?{id:'queued-shot',status:'prepared',references:[]}:{};
        return {ok:true,json:async()=>data};
    }};
    const {poll}=await load({registerExtension(){},graph,nodeOutputs:{}},{LiteGraph,FormData:class{}},api);
    await poll();
    await new Promise(resolve=>setTimeout(resolve,20));
    assert.equal(calls.filter(([path])=>path.endsWith('/jobs/queued-shot/start')).length,1);
    await poll();
    assert.equal(calls.filter(([path])=>path.endsWith('/jobs/queued-shot/start')).length,1);
});

test('middle drag over card controls is forwarded to native canvas while left click stays editable',async()=>{
    const handlers={};
    const root={addEventListener:(name,handler)=>handlers[name]=handler};
    const dispatched=[];
    class Pointer { constructor(type,init){Object.assign(this,init);this.type=type;} }
    const node={addDOMWidget:()=>({options:{}})};
    const {panelWidget}=await load({registerExtension(){},canvas:{canvas:{dispatchEvent:event=>dispatched.push(event)}}},
        {PointerEvent:Pointer,document:{createElement:()=>({})}});
    panelWidget(node,root,()=>100);
    const event={type:'pointerdown',button:1,buttons:4,pointerId:7,isPrimary:true,target:{closest:()=>true},preventDefault(){this.prevented=true},stopPropagation(){this.stopped=true}};
    handlers.pointerdown(event);
    assert.equal(event.prevented,true);
    assert.equal(dispatched.length,1);
    assert.equal(dispatched[0].pointerId,7);
    const left={...event,button:0,buttons:1,prevented:false,stopped:false};
    handlers.pointerdown(left);
    assert.equal(dispatched.length,1);
    assert.equal(left.stopped,true);
    assert.equal(left.prevented,false);
});

test('activity logs distinguish stages and hide credentials when copied',async()=>{
    const {activityLines,activityStatus}=await load({registerExtension(){}});
    const job={status:'unknown',kind:'text',stage:'shots',created_at:1,updated_at:4,error:'Bearer private-token sk-1234567890abcdef',events:[
        {time:1,status:'analysing',stage:'assets'}, {time:2,message:'接口响应 HTTP 200'}, {time:3,stage:'shots'}]};
    const log=activityLines(job);
    assert.match(log,/提取人物/);
    assert.match(log,/HTTP 200/);
    assert.match(log,/编写分镜/);
    assert.match(log,/结果未知/);
    assert.doesNotMatch(log,/private-token|1234567890abcdef/);
    assert.equal(activityStatus({...job,status:'succeeded'}),'已完成');
});

test('unchanged task status does not keep invalidating the canvas',async()=>{
    let dirty=0;
    const node={properties:{},agStatus:{textContent:'',classList:{toggle(){}}},setDirtyCanvas(){dirty++;}};
    const {message}=await load({registerExtension(){}});
    message(node,'已完成');
    message(node,'已完成');
    assert.equal(dirty,1);
    message(node,'正在生成',true);
    assert.equal(dirty,2);
});

test('compact log panel keeps logs when collapsed and cancels only the selected text task',async()=>{
    const elements=[];
    class Element {
        constructor(tag){this.tag=tag;this.children=[];this.events={};this.textContent='';this.scrollTop=0;this.scrollHeight=0;this.clientHeight=0;this.classList={toggle(){}};elements.push(this);}
        append(...items){this.children.push(...items);}
        replaceChildren(...items){this.children=items;}
        setAttribute(){}
        addEventListener(name,fn){this.events[name]=fn;}
    }
    const calls=[];
    const document={createElement:tag=>new Element(tag),body:new Element('body')};
    const api={fetchApi:async(path)=>{calls.push(path);return {ok:true,json:async()=>({id:'job-b',status:'cancelling',kind:'text',created_at:1})};}};
    const {createActivityPanel}=await load({registerExtension(){}},{document,FormData:class{},localStorage:{getItem:()=>null,setItem(){}},navigator:{clipboard:{writeText:async text=>calls.push(text)}}},api);
    const panel=createActivityPanel();
    const a={node:{id:1,title:'剧本 A'},job:{id:'job-a',kind:'text',status:'analysing',created_at:1,updated_at:2}};
    const b={node:{id:2,title:'剧本 B'},job:{id:'job-b',kind:'text',status:'analysing',created_at:1,updated_at:3,stage:'shots'}};
    panel.update([a,b]);
    const root=elements.find(e=>e.tag==='details');
    const log=elements.find(e=>e.tag==='pre');
    const collapse=elements.find(e=>e.textContent==='收起');
    const stop=elements.find(e=>e.textContent==='停止此任务');
    assert.equal(root.open,false);
    const before=log.textContent;
    root.open=true;
    await collapse.events.click();
    assert.equal(root.open,false);
    assert.equal(log.textContent,before);
    assert.equal(collapse.disabled,false);
    await stop.events.click();
    assert.deepEqual(calls,['/astrogate/storyboard/jobs/job-b/cancel']);
    assert.equal(stop.disabled,true);
    assert.equal(a.job.status,'analysing');
    panel.update([1,2,3,4].map(id=>({node:{id,title:'图片 '+id},job:{id:'image-'+id,kind:'image',status:id<4?'running':'queued',created_at:1}})));
    assert.ok(elements.some(e=>e.textContent==='分镜日志 · 3 张生成中 · 1 张等待'));
    assert.equal(stop.textContent,'停止此批次');
    panel.update([]);
    assert.match(log.textContent,/选择 TXT/);
});

test('building a chapter queues images once and the saved auto marker survives restore',async()=>{
    const {graph,LiteGraph,board}=analysisGraph();
    const recipe={project_id:'project',chapter_id:'chapter-id',layout_version:3,reference_wiring_version:3,nodes:['sheet','shot'].map((id,i)=>({id,type:'AstroGateStoryboardFrame',title:id,pos:[i*500,100],
        widgets:{prompt:id,job_id:'',aspect_ratio:'16:9',resolution:'1K',quality:'high'},
        properties:{astrogate_project:'project',astrogate_owner:id==='shot'?'shot':'actor',astrogate_owner_type:id==='shot'?'shot':'asset',astrogate_key:id,astrogate_kind:id==='shot'?'shot':'character',astrogate_view:id==='shot'?'':id,astrogate_base_prompt:id}})),
        links:[...['sheet','shot'].map(to=>({from:'$board',slot:0,to,input:'board',type:'ASTROGATE_BOARD'})),
            {from:'sheet',slot:0,to:'shot',input:'reference_images.reference_1',type:'IMAGE'}]};
    const calls=[];
    const api={fetchApi:async(path,options)=>{
        const body=options?.body?JSON.parse(options.body):null;
        calls.push([path,body]);
        const data=path.endsWith('/native/canvas')?recipe:path.endsWith('/native/prepare')?{id:'job-'+body.prompt,status:'prepared'}:{};
        return {ok:true,json:async()=>data};
    }};
    const app={registerExtension(){},graph,nodeOutputs:{},canvas:{ds:{},centerOnNode(){},selectNode(){}},graphToPrompt:async()=>({output:{},workflow:{}})};
    const {buildBoard}=await load(app,{LiteGraph,FormData:class{},crypto:{randomUUID:()=> 'auto-images'}},api);
    const info={project_id:'project',chapter_id:'chapter-id',analysis_id:'analysis'};
    await Promise.all([buildBoard(board,info),buildBoard(board,info)]);
    assert.equal(calls.filter(([p])=>p.endsWith('/native/canvas')).length,1);
    assert.equal(calls.filter(([p])=>p.endsWith('/start')).length,2);
    const prepared=calls.filter(([p])=>p.endsWith('/native/prepare')).map(([,b])=>b);
    assert.deepEqual(prepared.map(b=>b.prompt),['sheet','shot']);
    assert.equal(prepared.find(b=>b.prompt==='shot').references[0].job_id,'job-sheet');
    assert.equal(prepared.find(b=>b.prompt==='sheet').view,'sheet');
    assert.equal(board.properties.astrogate_generation_jobs.length,2);
    await buildBoard(board,info,true);
    assert.equal(calls.filter(([p])=>p.endsWith('/start')).length,2);
    board.properties.astrogate_autogenerate=false;
    await buildBoard(board,{...info,analysis_id:'another'});
    assert.equal(calls.filter(([p])=>p.endsWith('/start')).length,2);
});

test('completed chunks add cards without submitting images until analysis finishes',async()=>{
    const {graph,LiteGraph,board}=analysisGraph();
    const info={project_id:'project',chapter_id:'chapter-id',analysis_id:'analysis'};
    const item=id=>({id,type:'AstroGateStoryboardFrame',title:id,pos:[500,100],
        widgets:{prompt:id,job_id:'',aspect_ratio:'16:9',resolution:'1K',quality:'high'},
        properties:{astrogate_project:'project',astrogate_owner:id,astrogate_owner_type:'shot',astrogate_key:id,astrogate_kind:'shot',astrogate_base_prompt:id}});
    let ids=['shot1'];
    const calls=[];
    const api={fetchApi:async(path,options)=>{
        const body=options?.body?JSON.parse(options.body):null; calls.push([path,body]);
        const data=path.endsWith('/native/canvas')?{...info,layout_version:4,reference_wiring_version:4,nodes:ids.map(item),
            links:ids.map(to=>({from:'$board',slot:0,to,input:'board',type:'ASTROGATE_BOARD'}))}:
            path.endsWith('/native/prepare')?{id:'job-'+body.prompt,status:'prepared'}:{};
        return {ok:true,json:async()=>data};
    }};
    const app={registerExtension(){},graph,nodeOutputs:{},canvas:{ds:{},centerOnNode(){},selectNode(){}},graphToPrompt:async()=>({output:{},workflow:{}})};
    const {buildBoard}=await load(app,{LiteGraph,FormData:class{},crypto:{randomUUID:()=> 'partial-images'}},api);
    await buildBoard(board,info,false,true);
    assert.equal(graph._nodes.filter(n=>n.properties.astrogate_kind==='shot').length,1);
    assert.equal(calls.filter(([path])=>path==='/prompt').length,0);
    ids=['shot1','shot2'];
    await buildBoard(board,info,false,true);
    assert.equal(graph._nodes.filter(n=>n.properties.astrogate_kind==='shot').length,2);
    assert.equal(calls.filter(([path])=>path==='/prompt').length,0);
    await buildBoard(board,info);
    assert.equal(calls.filter(([path])=>path.endsWith('/start')).length,2);
    await buildBoard(board,info);
    assert.equal(calls.filter(([path])=>path.endsWith('/start')).length,2);
});

test('migrating an existing chapter simplifies wiring without starting a new image batch',async()=>{
    const {graph,LiteGraph,board}=analysisGraph();
    const info={project_id:'project',chapter_id:'chapter-id',analysis_id:'analysis'};
    board.properties.astrogate_board=info;
    const recipe={...info,reference_wiring_version:2,nodes:[{id:'shot',type:'AstroGateStoryboardFrame',title:'shot',pos:[500,100],
        widgets:{prompt:'shot',job_id:'',aspect_ratio:'16:9',resolution:'1K',quality:'high'},
        properties:{astrogate_project:'project',astrogate_owner:'shot',astrogate_key:'shot',astrogate_kind:'shot',astrogate_base_prompt:'shot'}}],
        links:[{from:'$board',slot:0,to:'shot',input:'board',type:'ASTROGATE_BOARD'}]};
    const calls=[];
    const api={fetchApi:async(path)=>{calls.push(path);return {ok:true,json:async()=>path.endsWith('/native/canvas')?recipe:{}};}};
    const app={registerExtension(){},graph,nodeOutputs:{},canvas:{ds:{},centerOnNode(){},selectNode(){}}};
    const {buildBoard}=await load(app,{LiteGraph,FormData:class{}},api);
    await buildBoard(board,info);
    assert.equal(board.properties.astrogate_wiring_version,2);
    assert.equal(calls.includes('/prompt'),false);
});

test('sheet migration removes old side cards and default props while preserving results and manual references',async()=>{
    const {graph,LiteGraph,board}=analysisGraph();
    const {mountGraph,changed,nodeSignature}=await load({registerExtension(){},graph,nodeOutputs:{}},{LiteGraph});
    const items=[['actor','character','front'],['actor::left','character','left'],['look','look',''],['scene','scene',''],['lamp','prop',''],['shot','shot','']]
        .map(([id,kind,view],i)=>({id,type:'AstroGateStoryboardFrame',title:id,pos:[i*500,0],
            widgets:{prompt:'old '+id,job_id:'job-'+id,aspect_ratio:'2:3',resolution:'4K',quality:'high'},
            properties:{astrogate_project:'project',astrogate_key:id,astrogate_owner:id==='actor::left'?'actor':id,
                astrogate_kind:kind,astrogate_view:view,astrogate_layout_version:2,astrogate_base_prompt:'old '+id,
                astrogate_images:[{filename:id+'.png'}]}}));
    const wire=(from,to,n)=>({from,to,slot:0,input:'reference_images.reference_'+n,type:'IMAGE'});
    const old={project_id:'project',chapter_id:'chapter-id',reference_wiring_version:2,nodes:items,
        links:[wire('actor','look',1),wire('actor::left','look',2),wire('scene','shot',1),wire('actor','shot',2),wire('actor::left','shot',3),wire('look','shot',4),wire('lamp','shot',5)]};
    const mapping=mountGraph(board,old);
    const actor=mapping.get('actor'),look=mapping.get('look'),shot=mapping.get('shot');
    const manual=LiteGraph.createNode('LoadImage');graph.add(manual);
    shot.addInput('reference_images.reference_6','IMAGE');manual.connect(0,shot,5);
    shot.properties.astrogate_reference_roles={'reference_images.reference_6':{source:String(manual.id),role:'style',detail:'手动画风'}};
    shot.properties.astrogate_signature=nodeSignature(shot);
    const next={...old,layout_version:3,reference_wiring_version:3,
        nodes:items.filter(item=>!['actor::left','lamp'].includes(item.id)).map(item=>({...item,
            widgets:{...item.widgets,prompt:['character','look'].includes(item.properties.astrogate_kind)?'new sheet '+item.id:item.widgets.prompt,aspect_ratio:'16:9'},
            properties:{...item.properties,astrogate_layout_version:3,astrogate_view:['character','look'].includes(item.properties.astrogate_kind)?'sheet':''}})),
        links:[wire('actor','look',1),wire('scene','shot',1),wire('look','shot',2)]};
    mountGraph(board,next,true);
    assert.equal(graph._nodes.some(node=>['actor::left','lamp'].includes(node.properties.astrogate_key)),false);
    assert.equal(board.properties.astrogate_previous_asset_layout.nodes.length,2);
    assert.equal(actor.properties.astrogate_previous_view.prompt,'old actor');
    assert.equal(actor.properties.astrogate_view,'sheet');
    assert.equal(actor.widgets[1].value,'job-actor');
    assert.equal(actor.properties.astrogate_images[0].filename,'actor.png');
    const sources=node=>node.inputs.filter(input=>input.link!=null).map(input=>graph.getNodeById(graph.links[input.link].origin_id));
    assert.deepEqual(sources(look),[actor]);
    assert.deepEqual(sources(shot),[mapping.get('scene'),look,manual]);
    assert.equal(shot.properties.astrogate_images[0].filename,'shot.png');
    assert.equal(changed(shot),false);
    assert.equal(changed(actor),true);
    assert.equal(shot.properties.astrogate_reference_roles['reference_images.reference_6'].detail,'手动画风');
    const enabled={...next,nodes:[...next.nodes,items.find(item=>item.id==='lamp')],links:[...next.links,wire('lamp','shot',2)]};
    const restored=mountGraph(board,enabled,true).get('lamp');
    assert.ok(sources(shot).includes(restored));
    assert.ok(sources(shot).includes(manual));
    assert.equal(board.properties.astrogate_previous_asset_layout.nodes.length,2);
    mountGraph(board,next,true);
    assert.equal(board.properties.astrogate_previous_asset_layout.nodes.length,3);
    assert.ok(sources(shot).includes(manual));
    const peopleOnly={...next,layout_version:4,reference_wiring_version:4,character_aliases:{look:'actor'},
        nodes:next.nodes.filter(item=>item.id!=='look').map(item=>({...item,properties:{...item.properties,astrogate_layout_version:4,
            ...(item.id==='actor'?{astrogate_look_id:'look',astrogate_look_version:'look-v1'}:{})}})),
        links:[wire('actor','shot',1)]};
    mountGraph(board,peopleOnly,true);
    assert.equal(graph._nodes.some(node=>node.properties.astrogate_kind==='look'),false);
    assert.deepEqual(sources(shot),[actor,manual]);
    assert.equal(actor.properties.astrogate_look_id,'look');
    assert.equal(actor.properties.astrogate_look_version,'look-v1');
    assert.equal(board.properties.astrogate_previous_asset_layout.nodes.length,4);
    assert.equal(shot.properties.astrogate_images[0].filename,'shot.png');
    assert.equal(changed(shot),false);
    shot.addInput('board','ASTROGATE_BOARD');board.connect(0,shot,shot.findInputSlot('board'));
    const rewritten={...peopleOnly,replace_shots:true,nodes:peopleOnly.nodes.map(item=>item.id==='shot'?{...item,id:'shot-new',
        properties:{...item.properties,astrogate_key:'shot-new',astrogate_owner:'shot-new'}}:item),links:[wire('actor','shot-new',1)]};
    const current=mountGraph(board,rewritten,true).get('shot-new');
    assert.equal(graph._nodes.includes(shot),false);
    assert.ok(graph._nodes.includes(current));
    const oldShot=board.properties.astrogate_previous_asset_layout.nodes.find(node=>node.properties.astrogate_key==='shot');
    assert.equal(oldShot.properties.astrogate_images[0].filename,'shot.png');
});

test('existing chapter wiring waits for running image jobs before migration',async()=>{
    const {graph,LiteGraph,board}=analysisGraph();
    board.properties.astrogate_board={project_id:'project',chapter_id:'chapter-id',analysis_id:'analysis'};
    board.properties.astrogate_layout_version=2;
    const calls=[];
    const jobs=[{id:'analysis',kind:'text',status:'succeeded',analysis_key:expectedAnalysisKey,chapter_id:'chapter-id'},
        {id:'image',kind:'image',status:'running',prompt_id:'prompt'}];
    const api=analysisAPI(calls,jobs);
    const app={registerExtension(){},graph,nodeOutputs:{},canvas:{ds:{},centerOnNode(){},selectNode(){}}};
    const {poll}=await load(app,{LiteGraph,FormData:class{}},api);
    await poll();
    assert.equal(calls.filter(([path])=>path.endsWith('/native/canvas')).length,0);
    jobs[1].status='succeeded';
    await poll();
    assert.equal(calls.filter(([path])=>path.endsWith('/native/canvas')).length,1);
});

test('global frame aspect leaves character sheets and scene masters independent and preserves old images',async()=>{
    const {graph,LiteGraph,board}=analysisGraph();
    board.widgets.push({name:'frame_aspect_ratio',value:'2.390：1'});
    const frames=['character','scene','shot'].map(kind=>{
        const n=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(n);
        n.properties={astrogate_kind:kind,astrogate_images:[{filename:'old.png'}]};
        n.widgets.find(w=>w.name==='aspect_ratio').value='16:9';
        n.addInput('board','ASTROGATE_BOARD');board.connect(0,n,0);return n;
    });
    const calls=[];
    const {applyFrameAspect}=await load({registerExtension(){},graph},{FormData:class{}},analysisAPI(calls,[]));
    await applyFrameAspect(board);
    assert.deepEqual(frames.map(n=>n.widgets.find(w=>w.name==='aspect_ratio').value),['16:9','16:9','2.39:1']);
    assert.ok(frames.every(n=>n.properties.astrogate_images[0].filename==='old.png'));
    assert.equal(JSON.parse(calls[0][1]).action,'frame_aspect_ratio');
    assert.equal(JSON.parse(calls[0][1]).style,'电影光线，雨夜\n第二行');
});

test('image card controls precede a folded prompt and batch progress counts only saved images',async()=>{
    const elements=[];
    class Element {
        constructor(tag){this.tag=tag;this.children=[];this.events={};this.classList={toggle(){}};elements.push(this);}
        append(...items){this.children.push(...items);}
        replaceChildren(...items){this.children=items;}
        addEventListener(name,fn){this.events[name]=fn;}
        getAttribute(name){return this[name];}
        removeAttribute(name){delete this[name];}
    }
    const {graph,LiteGraph}=graphMock();graph.change=()=>{};
    const node=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(node);
    node.properties.astrogate_images=[{filename:'old.png',type:'output'}];
    node.widgets.forEach(w=>{w.options={values:['16:9','4K','high']};});
    node.widgets[0].value='保留提示词';
    node.inputs=[{name:'reference_images.reference_1',link:11},{name:'reference_images.reference_2',link:22},{name:'board',link:33}];
    node.addDOMWidget=()=>({options:{}});node.setSize=size=>node.size=size;node.computeSize=()=>[430,440];
    let resize, disconnected=false;
    class ResizeObserver { constructor(callback){resize=callback;} observe(){} disconnect(){disconnected=true;} }
    const {installFrame,batchProgress}=await load({registerExtension(){},graph,nodeOutputs:{}},{ResizeObserver,document:{createElement:tag=>new Element(tag)}},{apiURL:path=>path});
    installFrame(node);
    const root=elements.find(e=>e.className?.startsWith('ag-native ag-image-card')).children[0];
    const detail=elements.find(e=>e.tag==='details');
    assert.equal(root.children[3].className,'ag-native-actions');
    assert.equal(root.children[5],detail);
    assert.match(root.children[0].textContent,/参考图 2/);
    assert.equal(node.properties.astrogate_references_folded,true);
    assert.equal(node.agStatus.hidden,true);
    const inputs=node.inputs;
    root.children[0].events.click();
    assert.equal(node.properties.astrogate_references_folded,false);
    assert.ok(node.inputs.every(i=>i.pos===undefined));
    root.children[0].events.click();
    assert.equal(node.inputs,inputs);
    assert.deepEqual(node.inputs.map(i=>i.link),[11,22,33]);
    assert.equal(detail.open,false);
    assert.equal(node.agImage.hidden,false);
    assert.equal(elements.find(e=>e.tag==='textarea').value,'保留提示词');
    detail.open=true;detail.events.toggle();root.offsetHeight=500;resize();
    const expandedHeight=node.size[1];
    detail.open=false;detail.events.toggle();root.offsetHeight=335;resize();
    assert.ok(expandedHeight-node.size[1]>=160);
    assert.ok(node.size[1]<440);
    node.widgets.push({name:'$$canvas-image-preview'},{name:'$$node-text-preview'});
    node.onDrawBackground();
    assert.ok(node.widgets.slice(-2).every(w=>w.hidden));
    assert.equal(node.widgets[0].value,'保留提示词');
    assert.equal(node.agImage.hidden,false);
    node.properties.astrogate_generation_jobs=['a','b','c','d'];
    batchProgress(node,new Map([['a',{status:'succeeded'}],['b',{status:'running'}],['c',{status:'queued'}],['d',{status:'unknown'}]]));
    assert.equal(node.agProgress.bar.value,1);assert.equal(node.agProgress.bar.max,4);
    assert.match(node.agProgress.label.textContent,/已完成 1\/4.*未完成\/异常 1/);
    node.properties.astrogate_kind='prop';node.agRefresh();node.agRefresh();
    assert.match(node.widgets[0].value,/纯白或浅灰.*不画人物.*不绘制使用场景/);
    assert.equal(node.widgets[0].value.split('物品设定素材：').length,2);
    assert.equal(elements.find(e=>e.tag==='textarea').value,node.widgets[0].value);
    assert.equal(node.properties.astrogate_images[0].filename,'old.png');
    const voiceButton=elements.find(e=>e.textContent==='绑定角色声线');
    assert.equal(voiceButton.hidden,true);
    node.properties.astrogate_kind='character';node.agRefresh();
    assert.equal(voiceButton.hidden,false);
    node.onRemoved();assert.equal(disconnected,true);
});

test('legacy cards preserve cached images while style, atmosphere and reference purposes affect generation',async()=>{
    const elements=[];
    class Element {
        constructor(tag){this.tag=tag;this.children=[];this.events={};this.classList={toggle(){}};elements.push(this);}
        append(...items){this.children.push(...items);}
        replaceChildren(...items){this.children=items;}
        addEventListener(name,fn){this.events[name]=fn;}
        getAttribute(name){return this[name];}
        removeAttribute(name){delete this[name];}
    }
    class ResizeObserver {observe(){} disconnect(){}}
    const {graph,LiteGraph,board}=analysisGraph();
    const image=LiteGraph.createNode('LoadImage');graph.add(image);image.title='参考图';
    const node=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(node);
    node.title='第 1 镜';node.properties={astrogate_kind:'shot',astrogate_project:'project',astrogate_owner:'shot',astrogate_owner_type:'shot',astrogate_base_prompt:'两人交谈'};
    Object.assign(node.widgets[0],{value:'两人交谈'});node.widgets[1].value='old-job';
    node.widgets[3].value='16:9';node.widgets[4].value='4K';node.widgets[5].value='high';
    node.properties.astrogate_base_settings=['16:9','4K','high'];
    node.widgets.forEach(w=>{w.options={values:['16:9','4K','high']};});
    node.addInput('reference_images.reference_1','IMAGE');image.connect(0,node,0);
    node.addInput('board','ASTROGATE_BOARD');board.connect(0,node,1);
    node.addDOMWidget=()=>({options:{}});node.setSize=size=>node.size=size;node.computeSize=()=>[430,440];
    const calls=[];
    const api={apiURL:path=>path,fetchApi:async(path,options)=>{
        const body=options?.body ? JSON.parse(options.body) : null;
        calls.push([path,body]);
        const data=path.endsWith('/native/prepare')?{id:'new-job',status:'prepared'}:path.endsWith('/jobs/old-job')?{id:'old-job',status:'succeeded'}:{};
        return {ok:true,json:async()=>data};
    }};
    const app={registerExtension(){},graph,nodeOutputs:{},graphToPrompt:async()=>({output:{},workflow:{}})};
    const {installFrame,changed,generate,nodeSignature}=await load(app,{ResizeObserver,document:{createElement:tag=>new Element(tag)},FormData:class{},crypto:{randomUUID:()=> 'prompt-id'}},api);
    node.properties.astrogate_signature=JSON.stringify(JSON.parse(nodeSignature(node)).slice(0,6));
    installFrame(node);
    assert.equal(changed(node),false);
    assert.equal(JSON.parse(node.properties.astrogate_signature).length,9);
    const style=board.widgets.find(w=>w.name==='style');
    const originalStyle=style.value;
    style.value='水墨风格';assert.equal(changed(node),true);
    style.value=originalStyle;assert.equal(changed(node),false);
    const root=elements.find(e=>e.className?.startsWith('ag-native ag-image-card')).children[0];
    assert.match(root.children[0].textContent,/分镜.*参考图 1/);
    root.children[0].events.click();
    const row=root.children[1].children[0];
    row.children[1].value='style';row.children[1].events.change();
    row.children[2].value='仅参考中国水墨笔触';row.children[2].events.input();
    const atmosphere=elements.find(e=>e.placeholder==='可选，例如雨雾、烛光或魔法光效');
    atmosphere.value='窗外雨雾';atmosphere.events.input();
    assert.equal(changed(node),true);
    await generate([node]);
    const prepared=calls.find(([path])=>path.endsWith('/native/prepare'))[1];
    assert.equal(prepared.atmosphere,'窗外雨雾');
    assert.equal(prepared.references[0].role,'style');
    assert.match(prepared.references[0].purpose,/整体画风.*仅参考中国水墨笔触/);
    assert.equal(calls.find(([path])=>path.endsWith('/edit'))[1].style,originalStyle);
    assert.equal(changed(node),false);
    const other=LiteGraph.createNode('LoadImage');graph.add(other);other.title='另一张参考图';other.connect(0,node,0);
    assert.equal(changed(node),true);
    await generate([node]);
    const replaced=calls.filter(([path])=>path.endsWith('/native/prepare')).at(-1)[1].references[0];
    assert.equal(replaced.role,'auto');
    assert.doesNotMatch(replaced.purpose,/中国水墨笔触/);
});

test('legacy signature migration does not hide edits or reference changes',async()=>{
    const {graph,LiteGraph}=graphMock();
    const node=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(node);
    node.properties={astrogate_kind:'shot',astrogate_base_prompt:'原提示词'};
    node.widgets[0].value='原提示词';
    const {changed,nodeSignature,upgradeSignature}=await load({registerExtension(){},graph});
    node.properties.astrogate_signature=JSON.stringify(JSON.parse(nodeSignature(node)).slice(0,6));
    const previous=node.properties.astrogate_signature;
    node.widgets[0].value='修改后的提示词';
    upgradeSignature(node);
    assert.equal(node.properties.astrogate_signature,previous);
    assert.equal(changed(node),true);
});

test('changing global style marks shots changed but keeps completed assets reusable',async()=>{
    const {graph,LiteGraph,board}=analysisGraph();
    const asset=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(asset);
    asset.properties={astrogate_kind:'character',astrogate_base_prompt:'人物'};
    asset.widgets[0].value='人物';asset.addInput('board','ASTROGATE_BOARD');board.connect(0,asset,0);
    const shot=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(shot);
    shot.properties={astrogate_kind:'shot',astrogate_base_prompt:'分镜'};
    shot.widgets[0].value='分镜';shot.addInput('board','ASTROGATE_BOARD');board.connect(0,shot,0);
    const {changed,nodeSignature}=await load({registerExtension(){},graph});
    asset.properties.astrogate_signature=nodeSignature(asset);
    shot.properties.astrogate_signature=nodeSignature(shot);
    board.widgets.find(w=>w.name==='style').value='水墨风格';
    assert.equal(changed(asset),false);
    assert.equal(changed(shot),true);
});

test('extra scene angles are manual independent nodes referencing one base scene',async()=>{
    const {graph,LiteGraph,board}=analysisGraph();
    board.properties.astrogate_board={project_id:'project',chapter_id:'chapter'};
    const source=LiteGraph.createNode('AstroGateStoryboardFrame');graph.add(source);
    source.title='客栈';source.pos=[500,100];source.size=[430,400];
    source.properties={astrogate_project:'project',astrogate_key:'scene',astrogate_owner:'scene',astrogate_owner_type:'asset',astrogate_kind:'scene',astrogate_version:'v1',astrogate_images:[{filename:'base.png'}]};
    source.widgets[0].value='无人客栈';source.widgets[1].value='base-job';
    source.addInput('board','ASTROGATE_BOARD');board.connect(0,source,0);
    const app={registerExtension(){},graph,canvas:{ds:{},centerOnNode(){},selectNode(){}}};
    const calls=[];
    const {addSceneAngle}=await load(app,{LiteGraph,FormData:class{}},analysisAPI(calls,[]));
    assert.equal(graph._nodes.length,3);
    const left=await addSceneAngle(source,'scene_left');
    assert.equal(left.properties.astrogate_owner,'scene');
    assert.equal(left.properties.astrogate_view,'scene_left');
    assert.equal(left.widgets[1].value,'');
    assert.equal(left.properties.astrogate_images.length,0);
    assert.match(left.widgets[0].value,/左侧机位.*明显不同的透视和裁切/);
    assert.match(left.widgets[0].value,/不镜像翻转或改变建筑、家具/);
    const ref=left.inputs.find(i=>i.name==='reference_images.reference_1');
    assert.equal(graph.links[ref.link].origin_id,source.id);
    assert.equal(await addSceneAngle(source,'scene_left'),left);
    assert.equal(graph._nodes.length,4);
    assert.equal(calls.filter(([p])=>p.endsWith('/native/prepare')).length,0);
    assert.equal(source.widgets[1].value,'base-job');
});
