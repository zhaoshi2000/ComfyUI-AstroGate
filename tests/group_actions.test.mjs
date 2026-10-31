import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../web/group_actions.js", import.meta.url), "utf8");

function fixture() {
    const graph = {
        nodes: [], groups: [], links: {1: {origin_id: 1, target_id: 2}}, changes: [],
        beforeChange() { this.changes.push("before"); }, afterChange() { this.changes.push("after"); },
        add(group) { group.graph = this; this.groups.push(group); },
        remove(group) { this.groups = this.groups.filter(item => item !== group); group.graph = null; },
    };
    class Group {
        constructor(title) { this.title = title; this.nodes = []; this.children = new Set(); }
        resizeTo(items) { this.items = [...items]; }
        recomputeInsideNodes() {
            this.nodes = this.items.flatMap(item => graph.nodes.includes(item) ? [item] : item.nodes);
            this.children = new Set(this.items);
        }
        move(x, y) { for (const node of this.nodes) { node.pos[0] += x; node.pos[1] += y; } }
    }
    const canvas = {graph, selectedItems: new Set(), selectItems(items) { this.selectedItems = new Set(items); }, setDirty() {}};
    const app = {canvas, registerExtension(extension) { this.extension = extension; }};
    return {graph, canvas, app, Group};
}

async function load(f) {
    const context = vm.createContext({LiteGraph: {LGraphGroup: f.Group}});
    const module = new vm.SourceTextModule(source + "\nexport { groupSelection, ungroupSelection };", {context});
    await module.link(() => new vm.SyntheticModule(["app"], function () { this.setExport("app", f.app); }, {context}));
    await module.evaluate();
    return module.namespace;
}

test("selected nodes form a native group that moves together and can be split without losing links", async () => {
    const f = fixture();
    const nodes = [{id: 1, pos: [10, 20]}, {id: 2, pos: [250, 20]}];
    f.graph.nodes.push(...nodes);
    f.canvas.selectItems(nodes);
    const {groupSelection, ungroupSelection} = await load(f);
    assert.equal(groupSelection(), true);
    const group = f.graph.groups[0];
    assert.equal(group.title, "新分组");
    assert.deepEqual(group.items, nodes);
    assert.deepEqual([...f.canvas.selectedItems], [group]);
    group.move(80, 50);
    assert.deepEqual(nodes.map(node => node.pos), [[90, 70], [330, 70]]);
    assert.equal(ungroupSelection(), true);
    assert.equal(f.graph.groups.length, 0);
    assert.deepEqual([...f.canvas.selectedItems], nodes);
    assert.deepEqual(f.graph.links, {1: {origin_id: 1, target_id: 2}});
    assert.deepEqual(f.graph.changes, ["before", "after", "before", "after"]);
});

test("splitting an outer group leaves an inner group intact", async () => {
    const f = fixture();
    const node = {id: 1, pos: [0, 0]};
    f.graph.nodes.push(node);
    const inner = new f.Group("内层");
    inner.resizeTo([node]); f.graph.add(inner); inner.recomputeInsideNodes();
    const outer = new f.Group("外层");
    outer.resizeTo([inner]); f.graph.add(outer); outer.recomputeInsideNodes();
    f.canvas.selectItems([outer, inner]);
    const {ungroupSelection} = await load(f);
    assert.equal(ungroupSelection(), true);
    assert.deepEqual(f.graph.groups, [inner]);
    assert.deepEqual(f.graph.nodes, [node]);
});

test("canvas menu exposes grouping actions and does nothing when nothing is selected", async () => {
    const f = fixture();
    await load(f);
    const items = f.app.extension.getCanvasMenuItems();
    assert.deepEqual(Array.from(items, item => item.content), ["框选节点成组", "拆分选中组"]);
    assert.equal(items[0].callback(), false);
    assert.equal(items[1].callback(), false);
});
