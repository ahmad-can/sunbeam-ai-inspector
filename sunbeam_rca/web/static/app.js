/* Sunbeam Auto-Triager — Frontend Logic */

(function () {
  "use strict";

  // ── DOM refs ──────────────────────────────────────────────
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => document.querySelectorAll(s);

  const inputSection = $("#input-section");
  const progressSection = $("#progress-section");
  const reportSection = $("#report-section");
  const reportContent = $("#report-content");
  const candidatesList = $("#candidates-list");
  const btnAnalyze = $("#btn-analyze");
  const inputError = $("#input-error");
  const progressStatus = $("#progress-status");

  const dropPipeline = $("#drop-pipeline");
  const dropSosreport = $("#drop-sosreport");
  const pipelineInput = $("#pipeline-input");
  const sosreportInput = $("#sosreport-input");
  const pipelineFileName = $("#pipeline-file-name");
  const sosreportFileName = $("#sosreport-file-name");
  const testrunUrl = $("#testrun-url");

  let pipelineFile = null;
  let sosreportFile = null;
  let currentJobId = null;
  let reportData = null;

  // ── Tabs ──────────────────────────────────────────────────
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$(".tab").forEach((t) => t.classList.remove("active"));
      $$(".tab-panel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      $(`#tab-${tab.dataset.tab}`).classList.add("active");
    });
  });

  // ── Drop Zones ────────────────────────────────────────────
  function setupDropZone(zone, input, onFile) {
    zone.addEventListener("click", () => input.click());

    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      zone.classList.add("dragover");
    });

    zone.addEventListener("dragleave", () => {
      zone.classList.remove("dragover");
    });

    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
      const file = e.dataTransfer.files[0];
      if (file) onFile(file);
    });

    input.addEventListener("change", () => {
      if (input.files[0]) onFile(input.files[0]);
    });
  }

  setupDropZone(dropPipeline, pipelineInput, (file) => {
    pipelineFile = file;
    pipelineFileName.textContent = file.name;
    dropPipeline.classList.add("has-file");
  });

  setupDropZone(dropSosreport, sosreportInput, (file) => {
    sosreportFile = file;
    sosreportFileName.textContent = file.name;
    dropSosreport.classList.add("has-file");
  });

  // ── Analyze ───────────────────────────────────────────────
  btnAnalyze.addEventListener("click", startAnalysis);

  async function startAnalysis() {
    inputError.textContent = "";

    const activeTab = $(".tab.active").dataset.tab;
    let body;

    if (activeTab === "url") {
      const url = testrunUrl.value.trim();
      if (!url) {
        inputError.textContent = "Please enter a test run URL.";
        return;
      }
      body = new FormData();
      body.append("test_run_url", url);
    } else {
      if (!pipelineFile && !sosreportFile) {
        inputError.textContent = "Please upload at least one file.";
        return;
      }
      body = new FormData();
      if (pipelineFile) body.append("pipeline_zip", pipelineFile);
      if (sosreportFile) body.append("sosreport", sosreportFile);
    }

    btnAnalyze.disabled = true;
    btnAnalyze.textContent = "Starting...";

    try {
      const resp = await fetch("/api/analyze", { method: "POST", body });
      const data = await resp.json();

      if (!resp.ok) {
        inputError.textContent = data.error || "Failed to start analysis.";
        btnAnalyze.disabled = false;
        btnAnalyze.textContent = "Analyze";
        return;
      }

      currentJobId = data.job_id;
      showProgress();
      connectSSE(data.job_id);
    } catch (err) {
      inputError.textContent = "Connection error: " + err.message;
      btnAnalyze.disabled = false;
      btnAnalyze.textContent = "Analyze";
    }
  }

  // ── Progress ──────────────────────────────────────────────
  function showProgress() {
    progressSection.classList.remove("hidden");
    reportSection.classList.add("hidden");
    resetStepper();
    progressStatus.textContent = "Starting analysis...";
    window.scrollTo({ top: progressSection.offsetTop - 20, behavior: "smooth" });
  }

  function resetStepper() {
    $$(".step-row").forEach((s) => {
      s.classList.remove("active", "done");
      const stat = s.querySelector(".step-stat");
      if (stat) stat.textContent = "";
    });
  }

  function setStepActive(index) {
    const step = $(`.step-row[data-index="${index}"]`);
    if (step && !step.classList.contains("done")) {
      step.classList.add("active");
    }
  }

  function setStepDone(index, stats) {
    const step = $(`.step-row[data-index="${index}"]`);
    if (!step) return;
    step.classList.remove("active");
    step.classList.add("done");

    const stat = step.querySelector(".step-stat");
    if (stats) {
      const parts = [];
      if (stats.event_count) parts.push(`${stats.event_count} events`);
      if (stats.match_count) parts.push(`${stats.match_count} matches`);
      if (stats.candidate_count) parts.push(`${stats.candidate_count} candidates`);
      if (stats.llm_discoveries != null) parts.push(`${stats.llm_discoveries} LLM discoveries`);
      stat.textContent = parts.join(" · ");
    }
  }

  // ── SSE ───────────────────────────────────────────────────
  const agentBadges = $("#agent-badges");
  const AGENT_NAMES = {
    infrastructure_agent: "Infra",
    network_agent: "Network",
    kubernetes_agent: "K8s",
    juju_agent: "Juju",
    storage_agent: "Storage",
    observability_agent: "Observability",
    pipeline_agent: "Pipeline",
  };
  const completedAgents = new Set();

  function addAgentBadge(name, status) {
    const label = AGENT_NAMES[name] || name;
    let badge = agentBadges.querySelector(`[data-agent="${name}"]`);
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "agent-badge";
      badge.dataset.agent = name;
      badge.textContent = label;
      agentBadges.appendChild(badge);
    }
    badge.classList.remove("running", "done");
    badge.classList.add(status);
  }

  function connectSSE(jobId) {
    completedAgents.clear();
    agentBadges.innerHTML = "";

    const source = new EventSource(`/api/stream/${jobId}`);

    source.addEventListener("node_start", (e) => {
      const data = JSON.parse(e.data);
      if (data.is_agent) {
        addAgentBadge(data.node, "running");
        setStepActive(data.index);
        progressStatus.textContent = `Running: ${data.label}...`;
      } else {
        setStepActive(data.index);
        progressStatus.textContent = `Running: ${data.label}...`;
      }
    });

    source.addEventListener("node_done", (e) => {
      const data = JSON.parse(e.data);
      if (data.is_agent) {
        addAgentBadge(data.node, "done");
        completedAgents.add(data.node);
        progressStatus.textContent = `Completed: ${data.label}`;
        const totalAgents = Object.keys(AGENT_NAMES).length;
        if (completedAgents.size >= totalAgents) {
          setStepDone(data.index, { match_count: completedAgents.size + " agents" });
        }
      } else {
        setStepDone(data.index, data);
        progressStatus.textContent = `Completed: ${data.label}`;
      }
    });

    source.addEventListener("report", (e) => {
      reportData = JSON.parse(e.data);
    });

    source.addEventListener("error", (e) => {
      try {
        const data = JSON.parse(e.data);
        progressStatus.textContent = "Error: " + (data.message || "Analysis failed");
        progressStatus.style.color = "var(--red)";
      } catch {
        // SSE connection error, not a data event
      }
    });

    source.addEventListener("done", () => {
      source.close();
      progressStatus.textContent = "Analysis complete!";
      progressStatus.style.color = "var(--green)";

      btnAnalyze.disabled = false;
      btnAnalyze.textContent = "Analyze";

      if (reportData) {
        renderReport(reportData);
      }
    });
  }

  // ── Report Rendering ─────────────────────────────────────
  function renderReport(data) {
    reportSection.classList.remove("hidden");

    const md = data.markdown || "";
    const mdForDisplay = stripRankedCandidates(md);
    reportContent.innerHTML = markdownToHtml(mdForDisplay);

    const candidates = data.json_report?.candidates || [];
    renderCandidates(candidates.slice(0, 10));

    $("#btn-download-md").onclick = () => downloadText(md, "report.md", "text/markdown");
    $("#btn-download-json").onclick = () =>
      downloadText(JSON.stringify(data.json_report, null, 2), "report.json", "application/json");

    window.scrollTo({ top: reportSection.offsetTop - 20, behavior: "smooth" });
  }

  function stripRankedCandidates(md) {
    const marker = "## Ranked Candidates";
    const idx = md.indexOf(marker);
    if (idx === -1) return md;
    return md.slice(0, idx).trimEnd();
  }

  function renderCandidates(candidates) {
    candidatesList.innerHTML = "";
    candidates.forEach((c, i) => {
      const card = document.createElement("div");
      card.className = "candidate" + (i === 0 ? " top" : "");

      const badgeClass = getBadgeClass(c.category);

      const evidenceHtml = (c.evidence || [])
        .slice(0, 4)
        .map((e) => {
          return `<div class="evidence-item">
            <span class="evidence-file">${esc(e.source_file)}:${e.line_number}</span>
            <span class="evidence-msg">${esc(e.message || "")}</span>
          </div>`;
        })
        .join("");

      card.innerHTML = `
        <div class="candidate-header">
          <span class="candidate-rank">${c.rank}</span>
          <span class="candidate-name">${esc(c.pattern_id)}</span>
          <span class="candidate-badge ${badgeClass}">${esc(c.category)}</span>
          <span class="candidate-confidence">${(c.confidence * 100).toFixed(0)}%</span>
          <span class="candidate-toggle">&#9662;</span>
        </div>
        <div class="candidate-body">
          <p class="candidate-desc">${esc(c.description)}</p>
          <p class="candidate-evidence-title">Evidence</p>
          ${evidenceHtml || '<p style="color:var(--text-muted)">No evidence available</p>'}
        </div>
      `;

      card.querySelector(".candidate-header").addEventListener("click", () => {
        card.classList.toggle("open");
      });

      candidatesList.appendChild(card);
    });
  }

  // ── Helpers ───────────────────────────────────────────────
  function getBadgeClass(category) {
    const map = {
      network: "badge-network",
      ceph: "badge-ceph",
      juju: "badge-juju",
      pipeline: "badge-pipeline",
      sunbeam: "badge-sunbeam",
      snap: "badge-snap",
      security: "badge-security",
      openstack: "badge-openstack",
    };
    return map[category] || "badge-default";
  }

  function truncate(s, len) {
    if (!s) return "";
    return s.length > len ? s.slice(0, len) + "..." : s;
  }

  function esc(s) {
    if (!s) return "";
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function downloadText(text, filename, mime) {
    const blob = new Blob([text], { type: mime });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function markdownToHtml(md) {
    if (!md) return "";
    const lines = md.split("\n");
    let html = "";
    let inCodeBlock = false;
    let inList = false;
    let listType = "";

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];

      if (line.startsWith("```")) {
        if (inCodeBlock) {
          html += "</code></pre>";
          inCodeBlock = false;
        } else {
          if (inList) { html += `</${listType}>`; inList = false; }
          html += "<pre><code>";
          inCodeBlock = true;
        }
        continue;
      }
      if (inCodeBlock) {
        html += esc(line) + "\n";
        continue;
      }

      if (inList && !line.match(/^(\s*[-*]|\s*\d+\.)\s/)) {
        html += `</${listType}>`;
        inList = false;
      }

      const headingMatch = line.match(/^(#{1,4})\s+(.+)/);
      if (headingMatch) {
        const level = headingMatch[1].length;
        html += `<h${level}>${inlineFormat(headingMatch[2])}</h${level}>`;
        continue;
      }

      const ulMatch = line.match(/^\s*[-*]\s+(.+)/);
      if (ulMatch) {
        if (!inList || listType !== "ul") {
          if (inList) html += `</${listType}>`;
          html += "<ul>";
          inList = true;
          listType = "ul";
        }
        html += `<li>${inlineFormat(ulMatch[1])}</li>`;
        continue;
      }

      const olMatch = line.match(/^\s*\d+\.\s+(.+)/);
      if (olMatch) {
        if (!inList || listType !== "ol") {
          if (inList) html += `</${listType}>`;
          html += "<ol>";
          inList = true;
          listType = "ol";
        }
        html += `<li>${inlineFormat(olMatch[1])}</li>`;
        continue;
      }

      const bqMatch = line.match(/^>\s*(.*)/);
      if (bqMatch) {
        html += `<blockquote>${inlineFormat(bqMatch[1])}</blockquote>`;
        continue;
      }

      if (!line.trim()) continue;

      html += `<p>${inlineFormat(line)}</p>`;
    }

    if (inCodeBlock) html += "</code></pre>";
    if (inList) html += `</${listType}>`;

    return html;
  }

  function inlineFormat(text) {
    return esc(text)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }
})();
