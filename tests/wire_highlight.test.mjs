import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../web/wire_highlight.js", import.meta.url), "utf8");

async function load() {
    const frames = new Map();
    let frameId = 0;
    const context = vm.createContext({
        requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
        cancelAnimationFrame(id) { frames.delete(id); },
    });
    const module = new vm.SourceTextModule(source, {context});
    await module.link(() => new vm.SyntheticModule(["app"], function () {
        this.setExport("app", {registerExtension() {}});
    }, {context}));
    await module.evaluate();
    return {api: module.namespace, flush() { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(fn => fn()); }};
}

function fixture() {
    const handlers = {};
    const captures = {};
    const element = {addEventListener(name, callback, capture) { handlers[name] = callback; captures[name] = capture; }};
    const links = new Map();
    const node = {getOutputPos: () => [0, 0], getInputPos: () => [100, 0]};
    const graph = {getLink: id => links.get(id), getNodeById: () => node, getNodeOnPos: () => null};
    const canvas = {canvas: element, graph, visible_nodes: [], renderedPaths: new Set(), ds: {scale: 2},
        convertEventToCanvasOffset: e => [e.clientX / 2, e.clientY / 2],
        drawConnections() {}, setDirty() {}};
    const hit = {isPointInStroke(path, x, y) { return x >= 0 && x <= 100 && Math.abs(y - path.y) <= this.lineWidth / 2; }};
    const ctx = {strokes: [], save() {}, restore() {}, stroke(path) { this.strokes.push({path: path.name, color: this.strokeStyle}); }};
    const event = (x, y, extra = {}) => ({target: element, clientX: x, clientY: y, button: 0, buttons: 0, pointerId: 1, ...extra});
    function link(id, y = 0) {
        const item = {id, origin_id: 1, target_id: 2, origin_slot: 0, target_slot: 0, path: {name: String(id), y}};
        links.set(id, item); canvas.renderedPaths.add(item); return item;
    }
    function click(x, y) { handlers.pointerdown(event(x,y)); handlers.pointerup(event(x,y)); }
    return {handlers, captures, element, graph, canvas, hit, ctx, event, link, click, links};
}

test("hover highlights the actual path; clicking pins it without swallowing canvas events", async () => {
    const {api, flush} = await load();
    const f = fixture();
    f.link(1);
    api.installWireHighlight(f.canvas, f.hit);
    assert.equal(f.captures.pointerdown, true);
    assert.equal(f.captures.pointerup, true);
    f.handlers.pointermove(f.event(100, 0)); flush();
    f.canvas.drawConnections(f.ctx);
    assert.deepEqual(f.ctx.strokes.map(s => s.color), ["#ffffff"]);
    f.ctx.strokes.length = 0;
    f.click(100, 0);
    f.handlers.pointermove(f.event(100, 70)); flush();
    f.canvas.drawConnections(f.ctx);
    assert.deepEqual(f.ctx.strokes.map(s => s.color), ["#42c9ff"]);
    f.ctx.strokes.length = 0;
    f.click(100, 70);
    f.canvas.drawConnections(f.ctx);
    assert.equal(f.ctx.strokes.length, 0);
});

test("overlapping links cycle on clicks and dragging or selecting nodes does not pin a line", async () => {
    const {api} = await load();
    const f = fixture();
    f.link(1); f.link(2);
    api.installWireHighlight(f.canvas, f.hit);
    f.click(100, 0); f.canvas.drawConnections(f.ctx);
    assert.deepEqual(f.ctx.strokes.map(s => s.path), ["2"]);
    f.ctx.strokes.length = 0;
    f.click(100, 0); f.canvas.drawConnections(f.ctx);
    assert.deepEqual(f.ctx.strokes.map(s => s.path), ["1"]);
    f.ctx.strokes.length = 0;
    f.handlers.pointerdown(f.event(100,0)); f.handlers.pointerup(f.event(120,0));
    f.canvas.drawConnections(f.ctx);
    assert.deepEqual(f.ctx.strokes.map(s => s.path), ["1"]);
    f.ctx.strokes.length = 0;
    f.graph.getNodeOnPos = () => ({});
    f.click(100, 0); f.canvas.drawConnections(f.ctx);
    assert.deepEqual(f.ctx.strokes.map(s => s.path), ["1"]);
});

test("rerouted paths follow their link and a different graph clears stale selection", async () => {
    const {api} = await load();
    const f = fixture();
    f.link(1, 20);
    f.canvas.renderedPaths.add({id: 99, pos: [50, 0], linkIds: new Set([1]), path: {name: "reroute", y: 0}});
    api.installWireHighlight(f.canvas, f.hit);
    f.click(70, 0);
    f.canvas.drawConnections(f.ctx);
    assert.deepEqual(f.ctx.strokes.map(s => s.path), ["1", "reroute"]);
    f.ctx.strokes.length = 0;
    f.canvas.graph = {...f.graph, getLink: () => undefined};
    f.canvas.drawConnections(f.ctx);
    assert.equal(f.ctx.strokes.length, 0);
});
