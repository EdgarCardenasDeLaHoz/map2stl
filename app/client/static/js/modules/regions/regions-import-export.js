/**
 * modules/regions-import-export.js — Import/export saved regions as JSON.
 *
 * Public API (all on window):
 *   window.exportRegionsJson()           — download all regions + saved settings
 *   window.importRegionsJsonFile(file)   — import regions from a JSON file
 */

function _downloadJson(filename, payload) {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

function _parseImportedRegions(payload) {
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.regions)) return payload.regions;
    return null;
}

function _validateRegionShape(region) {
    if (!region || typeof region !== 'object') {
        return { ok: false, error: 'Region must be an object' };
    }

    const required = ['name', 'north', 'south', 'east', 'west'];
    for (const key of required) {
        if (!(key in region)) {
            return { ok: false, error: `Missing required field: ${key}` };
        }
    }

    const north = Number(region.north);
    const south = Number(region.south);
    const east = Number(region.east);
    const west = Number(region.west);
    if ([north, south, east, west].some(Number.isNaN)) {
        return { ok: false, error: 'Bounding box values must be numeric' };
    }
    if (north <= south) {
        return { ok: false, error: 'north must be greater than south' };
    }
    if (east <= west) {
        return { ok: false, error: 'east must be greater than west' };
    }

    return {
        ok: true,
        region: {
            name: String(region.name),
            north,
            south,
            east,
            west,
            description: region.description ?? null,
            label: region.label ?? null,
            parameters: region.parameters && typeof region.parameters === 'object'
                ? region.parameters
                : undefined,
            settings: region.settings && typeof region.settings === 'object'
                ? region.settings
                : null,
        },
    };
}

async function exportRegionsJson() {
    const { data, error } = await window.api.regions.list();
    if (error) {
        window.showToast?.(`Export failed: ${error}`, 'error');
        return;
    }

    const regions = data?.regions || [];
    if (!regions.length) {
        window.showToast?.('No saved regions to export.', 'warning');
        return;
    }

    const regionsWithSettings = await Promise.all(regions.map(async (region) => {
        const { data: settingsData, error: settingsError } = await window.api.regions.getSettings(region.name);
        return {
            ...region,
            settings: settingsError ? {} : (settingsData?.settings || {}),
        };
    }));

    _downloadJson('regions.json', {
        version: 1,
        exported_at: new Date().toISOString(),
        region_count: regionsWithSettings.length,
        regions: regionsWithSettings,
    });

    window.showToast?.(`Exported ${regionsWithSettings.length} regions`, 'success');
}

async function importRegionsJsonFile(file) {
    if (!file) return;

    let parsed;
    try {
        parsed = JSON.parse(await file.text());
    } catch (error) {
        window.showToast?.(`Import failed: ${error.message || error}`, 'error');
        return;
    }

    const importedRegions = _parseImportedRegions(parsed);
    if (!importedRegions || !importedRegions.length) {
        window.showToast?.('Import failed: no regions found in JSON file.', 'error');
        return;
    }

    let imported = 0;
    let failed = 0;

    for (const rawRegion of importedRegions) {
        const checked = _validateRegionShape(rawRegion);
        if (!checked.ok) {
            failed += 1;
            continue;
        }

        const { settings, ...regionPayload } = checked.region;
        const { error } = await window.api.regions.create(regionPayload);
        if (error) {
            failed += 1;
            continue;
        }

        if (settings && Object.keys(settings).length > 0) {
            const { error: settingsError } = await window.api.regions.saveSettings(regionPayload.name, settings);
            if (settingsError) {
                failed += 1;
                continue;
            }
        }

        imported += 1;
    }

    await window.loadCoordinates?.();
    window.populateRegionsTable?.();

    if (imported > 0) {
        const message = failed > 0
            ? `Imported ${imported} regions (${failed} skipped)`
            : `Imported ${imported} regions`;
        window.showToast?.(message, failed > 0 ? 'info' : 'success');
        return;
    }

    window.showToast?.('Import failed: no regions were imported.', 'error');
}

window.exportRegionsJson = exportRegionsJson;
window.importRegionsJsonFile = importRegionsJsonFile;
