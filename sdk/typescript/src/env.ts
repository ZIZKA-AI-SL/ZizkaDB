/**
 * Environment variable, or undefined where the runtime has no `process` or a
 * `process` shim without `env` (some bundlers and edge runtimes).
 */
export function readEnv(name: string): string | undefined {
  return typeof process !== 'undefined' ? process.env?.[name] : undefined
}
