/*
 * Type-only guard (FE-H3): React Three Fiber's JSX elements stay strictly typed.
 *
 * `@react-three/fiber` v9 types every Three.js JSX element itself, through its
 * `ThreeElements` interface. A local augmentation that re-declares an element
 * replaces fiber's type for it. When the new type is `any`, or narrower than
 * fiber's, the compiler reports nothing, even from a `.ts` file, which
 * `skipLibCheck` never skips. The deleted `src/r3f.d.ts` did exactly that. It
 * imported `Object3DNode`, which v9 no longer exports, so all eight elements it
 * re-declared became `any`, and `skipLibCheck` hid the broken import. The
 * typecheck then accepted any prop on those elements, and a misspelled element
 * name too.
 *
 * Each probe below must FAIL to typecheck. If one of these eight elements is
 * loosened in the way its probe checks, that `@ts-expect-error` goes unused
 * (TS2578), `tsc -b` fails, and so does `npm run build`, which is a hard CI
 * gate. A loosening that leaves the probed fault an error, or one that touches
 * another element, goes unnoticed. Each probe line carries exactly one fault,
 * because `@ts-expect-error` accepts any error on its line.
 *
 * Nothing imports this file, so vite never bundles it. Its name does not match
 * vitest's `*.test.*` / `*.spec.*` glob, so vitest never runs it. `tsc -b`
 * compiles it (tsconfig.app.json includes all of `src`), and `eslint .` lints
 * it.
 */
import { Canvas } from "@react-three/fiber";

export function R3fJsxTypeProbe() {
    return (
        <Canvas>
            {/* @ts-expect-error -- fiber's mesh must reject an unknown prop */}
            <mesh bogusProp={1} />
            {/* @ts-expect-error -- fiber's points must reject an unknown prop */}
            <points bogusProp={1} />
            {/* @ts-expect-error -- the element is pointsMaterial; pointMaterial must not exist */}
            <pointMaterial size={2} />
            {/* @ts-expect-error -- fiber's group must reject an unknown prop */}
            <group bogusProp={1} />
            {/* @ts-expect-error -- torusGeometry's args are numbers, not strings */}
            <torusGeometry args={["x"]} />
            {/* @ts-expect-error -- fiber's sphereGeometry must reject an unknown prop */}
            <sphereGeometry bogusProp={1} />
            {/* @ts-expect-error -- fiber's meshBasicMaterial must reject an unknown prop */}
            <meshBasicMaterial bogusProp={1} />
            {/* @ts-expect-error -- fiber's ambientLight must reject an unknown prop */}
            <ambientLight bogusProp={1} />
        </Canvas>
    );
}
