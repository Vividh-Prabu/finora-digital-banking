(function () {
    "use strict";
    var script = document.currentScript || document.querySelector("script[data-url]");
    var canvas = document.getElementById("graph-canvas");
    if (!canvas || !script) return;
    var empty = document.getElementById("graph-empty");
    var legend = document.getElementById("graph-legend");

    var colors = {
        Customer: "#3b82f6", Account: "#10b981", Branch: "#f59e0b", Transaction: "#8b5cf6",
        LoanApplication: "#f97316", Loan: "#0d9488", LoanPayment: "#64748b"
    };

    function fail(message) {
        canvas.hidden = true;
        empty.textContent = message;
        empty.hidden = false;
    }
    if (typeof vis === "undefined") return fail("The graph library could not be loaded (check your internet connection).");

    fetch(script.dataset.url, { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) return fail("Graph data unavailable: " + data.error);
            if (!data.nodes.length) return fail("The graph is empty. Click “Sync from Firebase” to copy your data across.");

            var groups = {};
            Object.keys(colors).forEach(function (label) {
                groups[label] = { color: { background: colors[label], border: colors[label], highlight: { background: colors[label], border: "#0b1f33" } }, font: { color: "#0f172a" } };
            });
            data.nodes.forEach(function (n) {
                var d = document.createElement("div"); d.innerHTML = n.title; n.title = d;  // vis needs an element for HTML tooltips
            });
            new vis.Network(canvas, { nodes: new vis.DataSet(data.nodes), edges: new vis.DataSet(data.edges) }, {
                groups: groups,
                nodes: { shape: "dot", size: 14, font: { size: 12, face: "Inter, sans-serif" } },
                edges: { arrows: "to", color: { color: "#94a3b8", highlight: "#0d9488" }, font: { size: 9, color: "#64748b", strokeWidth: 0 }, smooth: { type: "continuous" } },
                physics: { stabilization: { iterations: 150 }, barnesHut: { gravitationalConstant: -4500, springLength: 110 } },
                interaction: { hover: true, tooltipDelay: 120 }
            });

            var present = {};
            data.nodes.forEach(function (n) { present[n.group] = true; });
            Object.keys(present).forEach(function (label) {
                var span = document.createElement("span");
                span.innerHTML = '<i class="dot" style="background:' + (colors[label] || "#94a3b8") + '"></i>' + label;
                legend.appendChild(span);
            });
        })
        .catch(function (e) { fail("Could not load graph data: " + e.message); });
})();
