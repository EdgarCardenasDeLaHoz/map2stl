/**
 * useAppStateBridge — Stage 7 composable
 *
 * Exposes the Pinia appStore as a thin bridge. Can be used in Vue components
 * to reactively read/write state that was previously only accessible via
 * window.appState.get() / window.appState.set().
 *
 * The actual bridge installation (snapshot + proxy replacement) is done in
 * main-vue.ts at startup. This composable is for use within Vue SFCs.
 */
import { useAppStore } from '../stores/app';

export function useAppStateBridge() {
    const store = useAppStore();
    return { store };
}
