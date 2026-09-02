/**
 * Build config for the stop-background-tasks client plugin.
 *
 * Uses the shared dsh client-bundle preset (shared/tsdown.client.ts): node-half
 * lib/ plus the browser bundle lib/client.js (closure-factory artifact for the
 * GUI's __ModuleLoader__, CSS Modules inlined with auto-injected
 * <style data-plugin>).
 *
 * Node-half entries point at src (tsdown compiles TS directly), so the build
 * needs no separate tsc emit for runtime artifacts.
 */
import { clientBundle } from '../../shared/tsdown.client.ts'

export default clientBundle('@linxin666/dsh-client-ui-bgstop', ['src/index.ts'])
