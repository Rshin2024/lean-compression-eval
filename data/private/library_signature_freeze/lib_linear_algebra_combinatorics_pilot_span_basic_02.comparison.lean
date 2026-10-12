

theorem _root_.CompressionEvalSignatureOriginal [Semiring S] [SMul S R] [Module S M] [IsScalarTower S R M] : span S (p : Set M) = p.restrictScalars S := span_eq (p.restrictScalars S)

theorem _root_.CompressionEvalSignatureMaterialized {«R» : Type u_1} {«M» : Type u_4} {«S» : Type u_7} [«inst» : Semiring.{u_1} R] [«inst_1» : AddCommMonoid.{u_4} M] [«inst_2» : Module.{u_1, u_4} R M] («p» : Submodule.{u_1, u_4} R M) [Semiring S] [SMul S R] [Module S M] [IsScalarTower S R M] : span S (p : Set M) = p.restrictScalars S := span_eq (p.restrictScalars S)

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
