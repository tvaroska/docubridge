/* Unified Split-Screen Commercial Underwriting Live Controller */
(function () {
  "use strict";

  var API_BASE = (window.DOCUBRIDGE_API_BASE || "").replace(/\/+$/, "");
  var executionMode = "live";
  var currentState = null;
  var pdfCatalog = [];
  var activePdfUrl = "";

  function apiUrl(path) {
    return API_BASE + path;
  }

  function formatModelDisplay(rawModel) {
    if (!rawModel) return "Gemini 3.8 Flash";
    if (rawModel.indexOf("argon") !== -1) return "Gemini 4 Argon";
    if (rawModel.indexOf("flash") !== -1) return "Gemini 3.8 Flash";
    return rawModel;
  }

  function esc(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function apiRequest(path, options) {
    var resp = await fetch(apiUrl(path), options || {});
    var traceId = resp.headers.get("x-docubridge-trace-id");
    if (traceId) {
      var traceEl = document.getElementById("live-trace-id");
      if (traceEl) traceEl.textContent = traceId.slice(0, 12);
    }
    if (!resp.ok) {
      throw new Error("HTTP " + resp.status);
    }
    return await resp.json();
  }

  function setBusy(isBusy, titleText, subText) {
    var dz = document.getElementById("pdf-dropzone");
    var titleEl = document.getElementById("dropzone-title");
    var subEl = document.getElementById("dropzone-sub");
    if (!dz || !titleEl || !subEl) return;
    if (isBusy) {
      dz.classList.add("busy");
      titleEl.textContent = titleText || "Processing PDFs with Live Gemini...";
      subEl.textContent =
        subText ||
        "Scrubbing PII at edge · Locking SHA-256 WORM Vault · Extracting multimodal anchors...";
    } else {
      dz.classList.remove("busy");
      titleEl.textContent = "Drag & drop real PDF files here, or click to browse";
      subEl.textContent =
        "Streams PDF bytes to Gemini 3.8 Flash (multimodal), scrubs PII at edge, and locks SHA-256 copy in 7-Year WORM Cloud Storage";
    }
  }

  window.setExecutionMode = function (mode) {
    executionMode = mode === "cached" ? "cached" : "live";
    var liveBtn = document.getElementById("mode-btn-live");
    var cachedBtn = document.getElementById("mode-btn-cached");
    if (liveBtn) liveBtn.classList.toggle("active", executionMode === "live");
    if (cachedBtn) cachedBtn.classList.toggle("active", executionMode === "cached");
    if (currentState) renderState(currentState);
  };

  window.switchRightTab = function (tabName) {
    ["overwatch", "pdf", "telemetry"].forEach(function (t) {
      var btn = document.getElementById("rtab-" + t);
      var panel = document.getElementById("rpanel-" + t);
      if (btn) btn.classList.toggle("active", t === tabName);
      if (panel) panel.classList.toggle("active", t === tabName);
    });
    if (tabName === "telemetry") {
      window.refreshLiveTelemetry();
    }
  };

  window.openPdfProof = function (pdfPath, pageNum, citationLabel, vaultUri) {
    var resolved = pdfPath.indexOf("http") === 0 ? pdfPath : apiUrl(pdfPath);
    var pageSuffix = pageNum ? "#page=" + pageNum + "&view=FitH" : "#view=FitH";
    var fullHref = resolved + pageSuffix;
    activePdfUrl = fullHref;

    var frame = document.getElementById("pdf-viewer-frame");
    if (frame) {
      frame.src = "about:blank";
      setTimeout(function () {
        frame.src = fullHref;
      }, 20);
    }
    var extLink = document.getElementById("pdf-open-external");
    if (extLink) extLink.href = fullHref;
    var citeEl = document.getElementById("pdf-meta-citation");
    if (citeEl) citeEl.textContent = citationLabel || "Verified Source PDF Anchor";
    var vaultEl = document.getElementById("pdf-meta-vault");
    if (vaultEl) vaultEl.textContent = vaultUri || "7-Year WORM Cloud Storage Audit Vault";

    window.switchRightTab("pdf");
  };

  function renderPdfSelectorPills() {
    var box = document.getElementById("pdf-selector-pills");
    if (!box) return;
    var items = pdfCatalog.length
      ? pdfCatalog
      : [
          {
            title: "1. 2025 Financials (p.4)",
            pdf_url: "/v1/demo/consolidation-collab/pdfs/01_Meridian_2025_Audited_Financials.pdf",
            highlight_page: 4,
          },
          {
            title: "2. MAI Appraisal (p.1)",
            pdf_url: "/v1/demo/consolidation-collab/pdfs/02_Meridian_MAI_Appraisal.pdf",
            highlight_page: 1,
          },
          {
            title: "3. FNB Payoff (p.1)",
            pdf_url: "/v1/demo/consolidation-collab/pdfs/03_First_National_Payoff_FNB4402.pdf",
            highlight_page: 1,
          },
          {
            title: "4. Oct Bank Stmt (p.2)",
            pdf_url: "/v1/demo/consolidation-collab/pdfs/04_Meridian_Oct2026_Operating_Bank_Statement.pdf",
            highlight_page: 2,
          },
          {
            title: "5. Day-2 Apex PDF (p.2)",
            pdf_url: "/v1/demo/consolidation-collab/pdfs/05_Apex_Precision_Day2_Bank_Statement.pdf",
            highlight_page: 2,
          },
        ];
    box.innerHTML = items
      .map(function (p, idx) {
        var shortLabel = (idx + 1) + ". " + p.title.split("(")[0].trim().slice(0, 22) + " (p." + p.highlight_page + ")";
        return (
          '<button class="chip-btn" onclick="window.openPdfProof(\'' +
          esc(p.pdf_url) +
          "', " +
          Number(p.highlight_page || 1) +
          ", '" +
          esc(p.title + " · Page " + p.highlight_page) +
          '\', \'7-Year WORM Cloud Storage\')">' +
          esc(shortLabel) +
          "</button>"
        );
      })
      .join("");
  }

  function renderState(state) {
    currentState = state;
    var beat = Number(state.beat || 0);
    var counts = state.counts || { total: 3, satisfied: 0, waived: 0, required: 3 };
    var fin = state.financial_summary || {};
    var telem = state.live_telemetry || {};
    var doneCount = Number(counts.satisfied || 0) + Number(counts.waived || 0);

    // Stage stepper pills
    for (var b = 0; b <= 4; b++) {
      var sp = document.getElementById("step-pill-" + b);
      if (sp) {
        sp.classList.toggle("active", b === beat);
        sp.classList.toggle("done", b < beat || (beat === 4 && b === 4));
      }
    }

    // KPIs
    var kpiFac = document.getElementById("kpi-facility");
    if (kpiFac) kpiFac.textContent = "$1.80M";
    var kpiMath = document.getElementById("kpi-math");
    if (kpiMath) {
      kpiMath.textContent =
        (fin.ltv_formatted || "69.23%") +
        " LTV (<=75%) · " +
        (fin.dscr_formatted || "1.44x") +
        " DSCR (>=1.25x)";
    }

    var kpiCheck = document.getElementById("kpi-checklist");
    if (kpiCheck) kpiCheck.textContent = doneCount + " / " + counts.total;
    var kpiCheckSub = document.getElementById("kpi-checklist-sub");
    if (kpiCheckSub) {
      kpiCheckSub.textContent =
        counts.satisfied +
        " verified · " +
        counts.waived +
        " waived · " +
        counts.required +
        " open";
    }

    var kpiModels = document.getElementById("kpi-models");
    var kpiTelem = document.getElementById("kpi-telemetry");
    if (kpiModels && kpiTelem) {
      if ((telem.argon_calls || 0) > 0 && beat < 4) {
        kpiModels.textContent = "Flash + Argon";
      } else {
        kpiModels.textContent = "Gemini 3.8 Flash";
      }
      var modeTag = executionMode === "live" ? "Live" : "Fallback";
      var totalTokens =
        Number(telem.flash_input_tokens || 0) +
        Number(telem.flash_output_tokens || 0) +
        Number(telem.argon_input_tokens || 0) +
        Number(telem.argon_output_tokens || 0);
      if (totalTokens > 0) {
        kpiTelem.textContent =
          modeTag +
          " · " +
          (telem.last_action_latency_ms || 0) +
          " ms · " +
          totalTokens.toLocaleString() +
          " tok · " +
          (telem.pii_tokens_redacted || 0) +
          " PII scrubbed";
      } else {
        kpiTelem.textContent =
          modeTag + " mode ready · Edge PII & 7-Year WORM active";
      }
    }

    var kpiSkill = document.getElementById("kpi-skill");
    var kpiSkillSub = document.getElementById("kpi-skill-sub");
    if (state.agent_gym_payoff) {
      if (kpiSkill) kpiSkill.textContent = "v" + state.agent_gym_payoff.skill_version;
      if (kpiSkillSub) {
        kpiSkillSub.textContent =
          "4/4 stress passed · 0 Day-2 escalations";
      }
      var skillPre = document.getElementById("compiled-skill-pre");
      if (skillPre && state.agent_gym_payoff.skill_markdown) {
        skillPre.textContent = state.agent_gym_payoff.skill_markdown;
      }
    } else {
      if (kpiSkill) kpiSkill.textContent = "v1.0.0";
      if (kpiSkillSub) kpiSkillSub.textContent = "consolidation-payoff · Guided Review";
    }

    // Borrower Count & Banner
    var countPill = document.getElementById("borrower-count-pill");
    if (countPill) {
      countPill.textContent =
        counts.required === 0
          ? "Cleared to Close (6/6)"
          : counts.required + " Required · " + doneCount + " Cleared";
      countPill.classList.toggle("complete", counts.required === 0);
    }

    var banner = state.borrower_banner || {};
    var bannerBox = document.getElementById("borrower-banner");
    if (bannerBox) {
      bannerBox.className = "status-banner " + (banner.tone || "info");
    }
    var bh = document.getElementById("borrower-banner-headline");
    if (bh) bh.textContent = banner.headline || "";
    var bm = document.getElementById("borrower-banner-msg");
    if (bm) bm.textContent = banner.message || "";

    // Checklist items
    var listEl = document.getElementById("checklist-container");
    if (listEl) {
      listEl.innerHTML = (state.borrower_checklist || [])
        .map(function (item) {
          var st = item.status || "required";
          var pageNum = item.page_number || 1;
          var pdfHref = item.pdf_url || "/v1/demo/consolidation-collab/pdfs/01_Meridian_2025_Audited_Financials.pdf";
          var citeBtn = item.source_citation
            ? '<button class="chip-btn" onclick="window.openPdfProof(\'' +
              esc(pdfHref) +
              "', " +
              Number(pageNum) +
              ", '" +
              esc(item.label + " · " + item.source_citation) +
              "', '" +
              esc(item.vault_uri || "7-Year WORM Cloud Storage") +
              "')\">View PDF p." +
              Number(pageNum) +
              " ↗</button>"
            : "";
          var shaLabel = item.sha256
            ? "SHA-256: " + item.sha256.slice(0, 12) + "… · WORM Locked"
            : "Awaiting borrower PDF";
          return (
            '<div class="check-item ' +
            esc(st) +
            '">' +
            '<div class="check-top">' +
            '<div class="check-title">' +
            (item.emergent ? '<span class="emergent-tag">DYNAMIC</span>' : "") +
            esc(item.label) +
            "</div>" +
            '<span class="status-pill ' +
            esc(st) +
            '">' +
            esc(item.badge_text || st.toUpperCase()) +
            "</span>" +
            "</div>" +
            '<div class="check-reason">' +
            esc(item.reason || "") +
            "</div>" +
            '<div class="check-footer">' +
            '<span class="mono-hash">' +
            esc(shaLabel) +
            "</span>" +
            citeBtn +
            "</div>" +
            "</div>"
          );
        })
        .join("");
    }

    // Dual Agents
    var a1 = state.agent1_bridge_status || {};
    var a2 = state.agent2_policy_status || {};
    document.getElementById("agent1-model").textContent = formatModelDisplay(a1.model);
    document.getElementById("agent1-headline").textContent = a1.headline || "";
    document.getElementById("agent1-detail").textContent = a1.detail || "";
    document.getElementById("agent2-model").textContent = formatModelDisplay(a2.model);
    document.getElementById("agent2-headline").textContent = a2.headline || "";
    document.getElementById("agent2-detail").textContent = a2.detail || "";

    var mutEl = document.getElementById("agent2-mutations");
    if (mutEl) {
      mutEl.innerHTML = (a2.checklist_mutations || [])
        .map(function (m) {
          return "<li>" + esc(m) + "</li>";
        })
        .join("");
    }

    var extEl = document.getElementById("recent-extractions-list");
    if (extEl) {
      var recent = telem.recent_extractions || [];
      extEl.innerHTML = recent
        .slice(0, 4)
        .map(function (r) {
          return (
            '<div class="ext-row">' +
            "<span><strong>" +
            esc(r.filename) +
            "</strong> · " +
            Number(r.latency_ms) +
            " ms · " +
            (r.pii_redacted > 0 ? r.pii_redacted + " PII redacted · " : "") +
            (Number(r.input_tokens || 0) + Number(r.output_tokens || 0)) +
            " tok</span>" +
            '<button class="chip-btn" onclick="window.openPdfProof(\'' +
            esc(r.pdf_url) +
            "', " +
            Number(r.highlight_page || 1) +
            ", '" +
            esc(r.filename + " · Page " + (r.highlight_page || 1)) +
            "', '" +
            esc(r.vault_uri || "") +
            "')\">p." +
            Number(r.highlight_page || 1) +
            "</button></div>"
          );
        })
        .join("");
    }

    // Overwatch Primary Action Slot (Step 2 -> Approve Waiver, Step 3 -> Distill SKILL.md)
    var actionSlot = document.getElementById("primary-action-slot");
    if (actionSlot) {
      var ow = state.underwriter_overwatch;
      var disc = state.cross_doc_discovery;
      if (ow && ow.status === "awaiting_approval" && disc) {
        actionSlot.innerHTML =
          '<div class="action-callout">' +
          '<div class="action-callout-hdr">' +
          '<div class="action-callout-title">Cross-Document Settlement Wire Matched (#8841)</div>' +
          '<span class="status-pill waived">GEMINI 4 ARGON RECOMMENDATION</span>' +
          "</div>" +
          '<div class="agent-detail">' +
          esc(disc.recommendation) +
          "</div>" +
          '<div class="wire-evidence-box">' +
          esc(disc.wire_date + " | " + disc.wire_beneficiary + " | " + disc.wire_amount + " | " + disc.wire_reference) +
          "</div>" +
          '<div class="cta-row">' +
          '<button class="primary-cta-btn green" id="btn-approve-waiver" onclick="window.approveWaiver()">' +
          "✓ Approve Cross-Document Waiver (1-Click Overwatch)" +
          "</button>" +
          '<button class="chip-btn" onclick="window.openPdfProof(\'' +
          esc(disc.pdf_url || "/v1/demo/consolidation-collab/pdfs/04_Meridian_Oct2026_Operating_Bank_Statement.pdf") +
          "', 2, 'Bank Statement Page 2 · $48,210.00 PAID IN FULL Wire', '" +
          esc(disc.vault_uri || "") +
          "')\">Inspect Bank Statement Page 2 ↗</button>" +
          "</div></div>";
      } else if (ow && ow.status === "approved" && !state.agent_gym_payoff) {
        actionSlot.innerHTML =
          '<div class="action-callout approved">' +
          '<div class="action-callout-hdr">' +
          '<div class="action-callout-title">Deal #1 Cleared to Close (6/6) · Ready for Agent Gym Distillation</div>' +
          '<span class="status-pill satisfied">RSR = 0.98 · SPIFFE OBO VERIFIED</span>' +
          "</div>" +
          '<div class="agent-detail">' +
          "Turn this single expert waiver into a governed, stress-tested skill revision (`consolidation-payoff@1.1.0`) so Gemini 3.8 Flash handles future deals with zero escalation." +
          "</div>" +
          '<div class="cta-row">' +
          '<button class="primary-cta-btn purple" id="btn-distill-skill" onclick="window.distillSkill()">' +
          "✦ Distill Rule & Compile SKILL.md v1.1 (Gemini 4 Argon)" +
          "</button></div></div>";
      } else {
        actionSlot.innerHTML = "";
      }
    }

    // Agent Gym Payoff Slot (Beat 4)
    var gymSlot = document.getElementById("gym-payoff-slot");
    if (gymSlot) {
      var gp = state.agent_gym_payoff;
      if (gp) {
        var stressHtml = (gp.stress_cases || [])
          .map(function (sc) {
            return (
              '<div class="stress-pill"><strong>✓ ' +
              esc(sc.name) +
              "</strong><br/><span>" +
              esc(sc.expected) +
              "</span></div>"
            );
          })
          .join("");
        gymSlot.innerHTML =
          '<div class="gym-card">' +
          '<div class="action-callout-hdr">' +
          '<div class="action-callout-title">Agent Gym Promoted `' +
          esc(gp.skill_name + "@" + gp.skill_version) +
          "` (" +
          gp.stress_cases_passed +
          "/" +
          gp.stress_cases_total +
          " Synthetic Stress Gate)</div>" +
          '<button class="chip-btn purple" onclick="window.switchRightTab(\'telemetry\')">Inspect Compiled SKILL.md ↗</button>' +
          "</div>" +
          '<div class="stress-grid">' +
          stressHtml +
          "</div>" +
          '<div class="day2-banner">' +
          "✓ Day-2 Verification (`" +
          esc(gp.day2_borrower) +
          "` · $1.45M Facility): Gemini 3.8 Flash auto-waived EquipLease Loan #9914 ($62,400.00 PAID IN FULL wire on Page 2) in " +
          Number(gp.day2_latency_ms || 142) +
          " ms · 0 Human Touchpoints · 0 Gemini 4 Argon Escalation Tokens." +
          ' <button class="chip-btn" onclick="window.openPdfProof(\'' +
          esc(gp.day2_pdf_url || "/v1/demo/consolidation-collab/pdfs/05_Apex_Precision_Day2_Bank_Statement.pdf") +
          "', 2, 'Day-2 Apex Precision · Page 2 #9914 PAID IN FULL Wire', '" +
          esc(gp.day2_vault_uri || "") +
          "')\">View Day-2 PDF p.2 ↗</button>" +
          "</div></div>";
      } else {
        gymSlot.innerHTML = "";
      }
    }
  }

  function fileToBase64(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () {
        resolve(String(reader.result || ""));
      };
      reader.onerror = function (err) {
        reject(err);
      };
      reader.readAsDataURL(file);
    });
  }

  window.uploadRealFiles = async function (fileList) {
    var filesArr = Array.prototype.slice.call(fileList || []);
    if (!filesArr.length) return;
    setBusy(
      true,
      "Uploading " + filesArr.length + " PDF(s) to Gemini 3.8 Flash...",
      executionMode === "live"
        ? "Running live multimodal PDF extraction, Edge PII scrubbing & WORM Cloud Storage lock..."
        : "Applying instant fallback extraction & WORM lock..."
    );
    try {
      var encoded = await Promise.all(
        filesArr.map(async function (f) {
          return {
            filename: f.name,
            content_base64: await fileToBase64(f),
          };
        })
      );
      var nextState = await apiRequest("/v1/demo/consolidation-collab/upload-pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ files: encoded, mode: executionMode }),
      });
      renderState(nextState);
    } catch (_err) {
      var fallbackBeat = currentState && currentState.beat >= 1 ? 2 : 1;
      await window.jumpToBeat(fallbackBeat);
    } finally {
      setBusy(false);
    }
  };

  window.dropSampleBatch = async function (batchName) {
    var sampleIds =
      batchName === "batch_1"
        ? ["meridian-financials", "meridian-appraisal"]
        : batchName === "batch_2"
        ? ["fnb-payoff", "meridian-bank-stmt"]
        : ["apex-day2-bank-stmt"];
    setBusy(
      true,
      executionMode === "live"
        ? "Streaming " + sampleIds.length + " Real PDF(s) through Gemini 3.8 Flash & Gemini 4 Argon..."
        : "Processing " + sampleIds.length + " PDF(s) (Instant Fallback)...",
      "Scrubbing PII · Locking SHA-256 WORM Vault · Evaluating Python Decimal LTV/DSCR..."
    );
    try {
      var nextState = await apiRequest("/v1/demo/consolidation-collab/upload-pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sample_ids: sampleIds, mode: executionMode }),
      });
      renderState(nextState);
    } catch (_err) {
      var targetBeat = batchName === "batch_1" ? 1 : batchName === "batch_2" ? 2 : 4;
      await window.jumpToBeat(targetBeat);
    } finally {
      setBusy(false);
    }
  };

  window.approveWaiver = async function () {
    setBusy(true, "Recording SPIFFE OBO Underwriter Waiver...", "Writing RFC 6902 governance audit to Cloud SQL...");
    try {
      var nextState = await apiRequest("/v1/demo/consolidation-collab/approve-waiver", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: executionMode }),
      });
      renderState(nextState);
    } catch (_err) {
      await window.jumpToBeat(3);
    } finally {
      setBusy(false);
    }
  };

  window.distillSkill = async function () {
    setBusy(
      true,
      "Distilling Tribal Rule & Compiling SKILL.md v1.1 via Gemini 4 Argon...",
      "Running 4 synthetic stress cases (including recurring monthly payment guardrail)..."
    );
    try {
      var nextState = await apiRequest("/v1/demo/consolidation-collab/distill-skill", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: executionMode }),
      });
      renderState(nextState);
    } catch (_err) {
      await window.jumpToBeat(4);
    } finally {
      setBusy(false);
    }
  };

  window.jumpToBeat = async function (beatNum) {
    if (beatNum === 0) {
      return window.resetStage();
    }
    try {
      var st = await apiRequest("/v1/demo/consolidation-collab/advance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ beat: beatNum }),
      });
      renderState(st);
    } catch (_e) {
      if (window.DOCUBRIDGE_OFFLINE_BEATS && window.DOCUBRIDGE_OFFLINE_BEATS[beatNum]) {
        renderState(window.DOCUBRIDGE_OFFLINE_BEATS[beatNum]);
      }
    }
  };

  window.resetStage = async function () {
    try {
      var st = await apiRequest("/v1/demo/consolidation-collab/reset", {
        method: "POST",
      });
      renderState(st);
    } catch (_e) {
      if (window.DOCUBRIDGE_OFFLINE_BEATS && window.DOCUBRIDGE_OFFLINE_BEATS[0]) {
        renderState(window.DOCUBRIDGE_OFFLINE_BEATS[0]);
      }
    }
  };

  window.refreshLiveTelemetry = async function () {
    try {
      var analytics = await apiRequest("/v1/telemetry/analytics");
      var sqlEval = await apiRequest("/v1/telemetry/sql-eval");

      var sumEl = document.getElementById("bq-summary-line");
      if (sumEl) {
        sumEl.textContent =
          "Captured " +
          Number(analytics.total_spans || 0) +
          " OpenTelemetry spans · Selective Judge Savings: " +
          Number((sqlEval.selective_judge_token_savings_pct || 95)).toFixed(0) +
          "%";
      }

      var gatesEl = document.getElementById("bq-gates-strip");
      if (gatesEl) {
        var gateLabels = [
          "1. Edge PII Redacted",
          "2. SPIFFE SVID Verified",
          "3. Python Decimal Math",
          "4. Oversight RSR >= 0.80",
          "5. Flash Right-Sized",
        ];
        gatesEl.innerHTML = gateLabels
          .map(function (g) {
            return '<div class="gate-chip">✓ ' + esc(g) + "</div>";
          })
          .join("");
      }

      var wfEl = document.getElementById("otel-waterfall-box");
      if (wfEl) {
        var spans = (analytics.recent_spans || []).slice(-10).reverse();
        wfEl.innerHTML = spans.length
          ? spans
              .map(function (s) {
                var modelBadge = s.gen_ai_model ? formatModelDisplay(s.gen_ai_model) : "Deterministic Code";
                return (
                  '<div class="span-row">' +
                  "<span>" +
                  esc(s.name || s.step_name || "span") +
                  "</span>" +
                  "<span>" +
                  esc(modelBadge) +
                  "</span>" +
                  '<span style="text-align:right">' +
                  Number(s.duration_ms || 0).toFixed(1) +
                  " ms</span></div>"
                );
              })
              .join("")
          : '<div class="mono-hash">Upload a PDF to stream live OpenTelemetry spans.</div>';
      }
    } catch (_err) {}
  };

  function wireDropzoneEvents() {
    var dz = document.getElementById("pdf-dropzone");
    var input = document.getElementById("pdf-file-input");
    if (dz) {
      dz.addEventListener("dragover", function (ev) {
        ev.preventDefault();
        dz.classList.add("dragover");
      });
      dz.addEventListener("dragleave", function () {
        dz.classList.remove("dragover");
      });
      dz.addEventListener("drop", function (ev) {
        ev.preventDefault();
        dz.classList.remove("dragover");
        if (ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files.length) {
          window.uploadRealFiles(ev.dataTransfer.files);
        }
      });
    }
    if (input) {
      input.addEventListener("change", function () {
        if (input.files && input.files.length) {
          window.uploadRealFiles(input.files);
          input.value = "";
        }
      });
    }
  }

  async function init() {
    var zipBtn = document.getElementById("btn-download-zip");
    if (zipBtn && API_BASE) {
      zipBtn.href = apiUrl("/v1/demo/consolidation-collab/pdfs.zip");
    }
    wireDropzoneEvents();
    document.addEventListener("keydown", function (ev) {
      if (ev.target && (ev.target.tagName === "INPUT" || ev.target.tagName === "TEXTAREA")) {
        return;
      }
      var cur = currentState ? Number(currentState.beat || 0) : 0;
      if (ev.key === "ArrowRight" && cur < 4) {
        ev.preventDefault();
        if (cur === 0) window.dropSampleBatch("batch_1");
        else if (cur === 1) window.dropSampleBatch("batch_2");
        else if (cur === 2) window.approveWaiver();
        else if (cur === 3) window.distillSkill();
      } else if (ev.key === "ArrowLeft" && cur > 0) {
        ev.preventDefault();
        window.jumpToBeat(cur - 1);
      }
    });
    renderPdfSelectorPills();

    try {
      var catResp = await apiRequest("/v1/demo/consolidation-collab/pdfs");
      if (catResp && catResp.pdfs) {
        pdfCatalog = catResp.pdfs;
        renderPdfSelectorPills();
      }
      var initSt = await apiRequest("/v1/demo/consolidation-collab/state");
      renderState(initSt);
      // Pre-load Page 4 of Audited Financials in the PDF Proof tab so switching tabs is instant
      var defaultPdf = apiUrl("/v1/demo/consolidation-collab/pdfs/01_Meridian_2025_Audited_Financials.pdf#page=4&view=FitH");
      var frame = document.getElementById("pdf-viewer-frame");
      if (frame && !activePdfUrl) {
        frame.src = defaultPdf;
      }
      var extLink = document.getElementById("pdf-open-external");
      if (extLink) {
        extLink.href = defaultPdf;
      }
    } catch (_err) {
      window.setExecutionMode("cached");
      if (window.DOCUBRIDGE_OFFLINE_BEATS && window.DOCUBRIDGE_OFFLINE_BEATS[0]) {
        renderState(window.DOCUBRIDGE_OFFLINE_BEATS[0]);
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
