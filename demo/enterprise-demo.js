import {
  FALLBACK_ASSETS,
  FALLBACK_SPIFFE_AGENTS,
  FALLBACK_TRUST_POLICIES,
  apiFetch,
  applyScenarioLabels,
  formatPerturbationStability,
  getActiveEntityId,
  getApiBase,
  initSharedState,
  loadScenarios,
  renderDomainSwitcher,
  scenarioForDomain,
  scenarioLabel,
  setCaption,
  stageScenario,
  switcherDomains,
  unlockDemoSession,
  updateTraceBadge,
} from "./modules/api_state.js";
import {
  runCompileSkill,
  runDistillSignal,
  runEvaluateRule,
  runPromoteTrustSlice,
  runResetGym,
  runRollbackSkill,
  runSimulateDrift,
  runVerifyDay2Payoff,
} from "./modules/agent_gym.js";
import {
  FALLBACK_COLLAB_STATE,
  advanceConsolidationCollab,
  refreshConsolidationCollab,
  renderConsolidationCollab,
  resetConsolidationCollab,
} from "./modules/consolidation_stage.js";
import {
  refreshScene2LineageAndSync,
  selectRoleView,
  switchDomain,
  switchScene,
} from "./modules/domain_scenes.js";
import {
  closeSourceProofModal,
  handleInboxApprove,
  openSourceProofModal,
  refreshLiveTelemetry,
  renderCatalogAssets,
  renderRegistryCatalog,
  renderSpiffeAgents,
  renderTrustPolicies,
  runGatewayCheck,
  selectRegistryTab,
  setPersonaMode,
  togglePersona,
} from "./modules/inbox_registry.js";
import {
  FALLBACK_SCHEMA_EVOL_STATE,
  advanceSchemaEvolutionCollab,
  promoteSchemaRefineryProposal,
  refreshSchemaEvolutionCollab,
  refreshSchemaRefinery,
  renderSchemaEvolutionCollab,
  renderSchemaRefineryRevisions,
  resetSchemaEvolutionCollab,
  runSchemaRefineryAssay,
  switchSchemaEvolutionScenario,
} from "./modules/schema_refinery_stage.js";
import {
  refreshOtelWaterfall,
  renderBqSqlEval,
  renderOtelAnalytics,
  runBqSqlTraceEval,
  runTracedPipelineSweep,
  switchBqSqlQueryTab,
} from "./modules/telemetry_xray.js";

initSharedState();

window.FALLBACK_COLLAB_STATE = FALLBACK_COLLAB_STATE;
window.FALLBACK_SCHEMA_EVOL_STATE = FALLBACK_SCHEMA_EVOL_STATE;

// Bind shared helpers and every HTML onclick controller on window
window.formatPerturbationStability = formatPerturbationStability;
window.scenarioForDomain = scenarioForDomain;
window.stageScenario = stageScenario;
window.scenarioLabel = scenarioLabel;
window.switcherDomains = switcherDomains;
window.renderDomainSwitcher = renderDomainSwitcher;
window.loadScenarios = loadScenarios;
window.applyScenarioLabels = applyScenarioLabels;
window.getApiBase = getApiBase;
window.getActiveEntityId = getActiveEntityId;
window.updateTraceBadge = updateTraceBadge;
window.unlockDemoSession = unlockDemoSession;
window.apiFetch = apiFetch;
window.setCaption = setCaption;

window.switchScene = switchScene;
window.switchDomain = switchDomain;
window.selectRoleView = selectRoleView;
window.refreshScene2LineageAndSync = refreshScene2LineageAndSync;

window.setPersonaMode = setPersonaMode;
window.togglePersona = togglePersona;
window.handleInboxApprove = handleInboxApprove;
window.openSourceProofModal = openSourceProofModal;
window.closeSourceProofModal = closeSourceProofModal;
window.renderCatalogAssets = renderCatalogAssets;
window.selectRegistryTab = selectRegistryTab;
window.runGatewayCheck = runGatewayCheck;
window.renderRegistryCatalog = renderRegistryCatalog;
window.renderSpiffeAgents = renderSpiffeAgents;
window.renderTrustPolicies = renderTrustPolicies;
window.refreshLiveTelemetry = refreshLiveTelemetry;

window.runDistillSignal = runDistillSignal;
window.runEvaluateRule = runEvaluateRule;
window.runCompileSkill = runCompileSkill;
window.runRollbackSkill = runRollbackSkill;
window.runPromoteTrustSlice = runPromoteTrustSlice;
window.runSimulateDrift = runSimulateDrift;
window.runVerifyDay2Payoff = runVerifyDay2Payoff;
window.runResetGym = runResetGym;

window.renderConsolidationCollab = renderConsolidationCollab;
window.refreshConsolidationCollab = refreshConsolidationCollab;
window.advanceConsolidationCollab = advanceConsolidationCollab;
window.resetConsolidationCollab = resetConsolidationCollab;

window.renderOtelAnalytics = renderOtelAnalytics;
window.switchBqSqlQueryTab = switchBqSqlQueryTab;
window.renderBqSqlEval = renderBqSqlEval;
window.runBqSqlTraceEval = runBqSqlTraceEval;
window.refreshOtelWaterfall = refreshOtelWaterfall;
window.runTracedPipelineSweep = runTracedPipelineSweep;

window.renderSchemaRefineryRevisions = renderSchemaRefineryRevisions;
window.refreshSchemaRefinery = refreshSchemaRefinery;
window.runSchemaRefineryAssay = runSchemaRefineryAssay;
window.promoteSchemaRefineryProposal = promoteSchemaRefineryProposal;
window.switchSchemaEvolutionScenario = switchSchemaEvolutionScenario;
window.renderSchemaEvolutionCollab = renderSchemaEvolutionCollab;
window.refreshSchemaEvolutionCollab = refreshSchemaEvolutionCollab;
window.advanceSchemaEvolutionCollab = advanceSchemaEvolutionCollab;
window.resetSchemaEvolutionCollab = resetSchemaEvolutionCollab;

// Delegated click handler for all .anchor-chip citations (Click-to-Source Document Proof Modal)
document.addEventListener("click", function(ev) {
  const chip = ev.target.closest(".anchor-chip");
  if (chip) {
    ev.preventDefault();
    openSourceProofModal(chip.textContent);
  }
});

async function initDemoConsole() {
  renderCatalogAssets(FALLBACK_ASSETS);
  renderSpiffeAgents(FALLBACK_SPIFFE_AGENTS);
  renderTrustPolicies(FALLBACK_TRUST_POLICIES);
  renderOtelAnalytics(null);
  await loadScenarios();
  const domains = switcherDomains();
  let initialDomain = domains[0] || "banking";
  try {
    const urlDom = (new URLSearchParams(window.location.search).get("domain") || "").trim().toLowerCase();
    const envDom = (window.DEFAULT_DEMO_DOMAIN || "").trim().toLowerCase();
    const candidate = urlDom || envDom;
    if (candidate && (domains.length === 0 || domains.includes(candidate))) {
      initialDomain = candidate;
    }
  } catch (_) {}
  switchDomain(initialDomain);
  renderConsolidationCollab(FALLBACK_COLLAB_STATE);
  renderSchemaEvolutionCollab(FALLBACK_SCHEMA_EVOL_STATE);
  apiFetch("/v1/demo/seed", { method: "POST" }).catch(() => {});
  refreshConsolidationCollab();
  refreshSchemaEvolutionCollab();
  refreshLiveTelemetry();
  refreshSchemaRefinery();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initDemoConsole);
} else {
  initDemoConsole();
}
