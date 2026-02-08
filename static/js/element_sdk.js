window.elementSdk = {
  config: {},
  init: function(options) {
    this.config = options.defaultConfig;
    // Immediately trigger config change so the UI updates
    if (options.onConfigChange) {
      options.onConfigChange(this.config);
    }
    this.options = options;
    console.log("Element SDK Initialized");
    return { isOk: true };
  },
  setConfig: function(newConfig) {
    this.config = { ...this.config, ...newConfig };
    if (this.options && this.options.onConfigChange) {
      this.options.onConfigChange(this.config);
    }
  }
};
