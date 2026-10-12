

theorem _root_.CompressionEvalSignatureOriginal (s : Set M) : (AddSubmonoid.closure s).toNatSubmodule = .span ℕ s := (Submodule.span_le.mpr AddSubmonoid.subset_closure).antisymm'
    ((Submodule.span ℕ s).toAddSubmonoid.closure_le.mpr Submodule.subset_span)

theorem _root_.CompressionEvalSignatureMaterialized {«M» : Type u_4} [«inst» : AddCommMonoid.{u_4} M] (s : Set M) : (AddSubmonoid.closure s).toNatSubmodule = .span ℕ s := (Submodule.span_le.mpr AddSubmonoid.subset_closure).antisymm'
    ((Submodule.span ℕ s).toAddSubmonoid.closure_le.mpr Submodule.subset_span)

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
