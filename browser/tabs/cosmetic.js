const cosmeticApi = globalThis.browser ?? globalThis.chrome
cosmeticApi.runtime.sendMessage({ type: 'cosmetic-adblock' }).catch(() => {})
