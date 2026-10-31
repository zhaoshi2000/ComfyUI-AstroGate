import { app } from "../../scripts/app.js";

function linkIds(segment, graph) {
    if (segment.linkIds) return [...segment.linkIds].filter(id => graph.getLink(id));
    return segment.origin_id != null && graph.getLink(segment.id) ? [segment.id] : [];
}

function nearEndpoint(link, graph, point, radius) {
    const start = graph.getNodeById(link.origin_id);
    const end = graph.getNodeById(link.target_id);
    const positions = [link.origin_slot >= 0 ? start?.getOutputPos(link.origin_slot) : null,
        link.target_slot >= 0 ? end?.getInputPos(link.target_slot) : null];
    return positions.some(pos => pos && Math.hypot(pos[0] - point[0], pos[1] - point[1]) < radius);
}

export function linksAtPoint(canvas, hitContext, point) {
    const graph = canvas.graph;
    if (!graph || graph.getNodeOnPos(point[0], point[1], canvas.visible_nodes)) return [];
    const scale = canvas.ds.scale || 1;
    const segments = [...canvas.renderedPaths].reverse();
    hitContext.lineCap = "round";
    for (const pixels of [4, 8, 14]) {
        hitContext.lineWidth = pixels / scale;
        const matches = [];
        for (const segment of segments) {
            if (!segment.path || (segment.linkIds && Math.hypot(segment.pos[0] - point[0], segment.pos[1] - point[1]) < 12 / scale)) continue;
            if (!hitContext.isPointInStroke(segment.path, point[0], point[1])) continue;
            for (const id of linkIds(segment, graph)) {
                const link = graph.getLink(id);
                if (link && !nearEndpoint(link, graph, point, 12 / scale) && !matches.includes(id)) matches.push(id);
            }
        }
        if (matches.length) return matches;
    }
    return [];
}

export function installWireHighlight(canvas, hitContext) {
    const element = canvas.canvas;
    let graph = canvas.graph, hovered = null, selected = null, down = null, pendingMove = null, frame = 0;
    const dirty = () => canvas.setDirty(false, true);
    function syncGraph() {
        if (canvas.graph !== graph) { graph = canvas.graph; hovered = selected = null; }
        if (selected != null && !graph?.getLink(selected)) selected = null;
        if (hovered != null && !graph?.getLink(hovered)) hovered = null;
    }
    function hits(event) {
        if (event.target !== element || !graph) return [];
        return linksAtPoint(canvas, hitContext, canvas.convertEventToCanvasOffset(event));
    }
    const drawConnections = canvas.drawConnections;
    canvas.drawConnections = function (ctx, ...args) {
        const result = drawConnections.call(this, ctx, ...args);
        syncGraph();
        if (selected == null && hovered == null) return result;
        ctx.save();
        ctx.lineCap = "round";
        for (const segment of this.renderedPaths) {
            if (!segment.path) continue;
            const ids = linkIds(segment, graph);
            const pinned = selected != null && ids.includes(selected);
            if (!pinned && (hovered == null || !ids.includes(hovered))) continue;
            ctx.lineWidth = (pinned ? 5 : 4) / (this.ds.scale || 1);
            ctx.strokeStyle = pinned ? "#42c9ff" : "#ffffff";
            ctx.shadowColor = "#30bfff";
            ctx.shadowBlur = 9 / (this.ds.scale || 1);
            ctx.globalAlpha = 1;
            ctx.stroke(segment.path);
        }
        ctx.restore();
        return result;
    };
    element.addEventListener("pointermove", event => {
        pendingMove = event;
        if (frame) return;
        frame = requestAnimationFrame(() => {
            frame = 0;
            syncGraph();
            const next = pendingMove.buttons ? null : (hits(pendingMove)[0] ?? null);
            if (next !== hovered) { hovered = next; dirty(); }
        });
    });
    element.addEventListener("pointerleave", () => {
        pendingMove = null;
        down = null;
        if (frame) cancelAnimationFrame(frame);
        frame = 0;
        if (hovered != null) { hovered = null; dirty(); }
    });
    element.addEventListener("pointerdown", event => {
        if (event.button !== 0) return;
        syncGraph();
        const point = canvas.convertEventToCanvasOffset(event);
        if (graph?.getNodeOnPos(point[0], point[1], canvas.visible_nodes)) { down = null; return; }
        down = {pointerId: event.pointerId, x: event.clientX, y: event.clientY, matches: hits(event)};
    }, true);
    element.addEventListener("pointerup", event => {
        if (!down || down.pointerId !== event.pointerId || event.button !== 0) return;
        const click = Math.hypot(event.clientX - down.x, event.clientY - down.y) <= 4;
        const matches = down.matches;
        down = null;
        if (!click) return;
        if (!matches.length) selected = null;
        else if (matches.length === 1) selected = selected === matches[0] ? null : matches[0];
        else selected = matches[(matches.indexOf(selected) + 1) % matches.length];
        dirty();
    }, true);
    element.addEventListener("pointercancel", () => { down = null; }, true);
}

app.registerExtension({
    name: "AstroGate.WireHighlight",
    setup() {
        const canvas = app.canvas;
        const hitContext = document.createElement("canvas").getContext("2d");
        if (canvas?.canvas && hitContext) installWireHighlight(canvas, hitContext);
    },
});
