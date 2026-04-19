/**
 * syntaxCheck.test.js — Validate that all JS source files parse without errors.
 *
 * Catches missing braces, unterminated strings, invalid tokens, and other
 * syntax-level bugs that are hard to detect without loading every module.
 *
 * Uses Node's `--check` flag via child_process to validate each file.
 */
import { describe, it, expect } from 'vitest';
import { execFileSync } from 'node:child_process';
import { readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

// Resolve paths relative to the test file location
const JS_ROOT = join(__dirname, '..', '..', 'app', 'client', 'static', 'js');

/**
 * Recursively collect all .js files under a directory.
 */
function collectJsFiles(dir) {
    const results = [];
    let entries;
    try {
        entries = readdirSync(dir);
    } catch {
        return results;
    }
    for (const entry of entries) {
        const full = join(dir, entry);
        // Skip node_modules, vendor, and built output
        if (entry === 'node_modules' || entry === 'vendor' || entry === 'dist') continue;
        try {
            const stat = statSync(full);
            if (stat.isDirectory()) {
                results.push(...collectJsFiles(full));
            } else if (entry.endsWith('.js') && !entry.endsWith('.min.js')) {
                results.push(full);
            }
        } catch {
            // skip inaccessible files
        }
    }
    return results;
}

// Collect all JS files (excluding vue/ which contains .ts and .vue)
const jsFiles = collectJsFiles(join(JS_ROOT, 'modules'))
    .concat(collectJsFiles(join(JS_ROOT, 'workers')));

// Also check the top-level JS files
for (const name of ['app.js', 'main.js']) {
    const full = join(JS_ROOT, name);
    try {
        statSync(full);
        jsFiles.push(full);
    } catch {
        // file doesn't exist
    }
}

describe('JS source syntax validation', () => {
    it.each(jsFiles.map(f => [relative(JS_ROOT, f), f]))(
        '%s parses without syntax errors',
        (_label, filePath) => {
            try {
                // --check parses but does not execute
                execFileSync(process.execPath, [
                    '--check',
                    filePath,
                ], { stdio: 'pipe' });
            } catch (err) {
                const stderr = err.stderr?.toString() || '';
                expect.fail(
                    `Syntax error in ${_label}:\n${stderr}`
                );
            }
        }
    );
});
