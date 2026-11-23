;(() => {
  // Preserve the loaded pool entry while the main app bundle is still downloading.
  const state = window.history.state;
  const origin = state?.usr?.connectedPoolOrigin;
  if (origin) window.__TMC_INITIAL_POOL_LOCATION__ = { key: state.key ?? "default", origin };
})();
