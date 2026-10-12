

theorem _root_.CompressionEvalSignatureOriginal (j) [Decidable (i = j)] : b.repr (b i) j = if i = j then 1 else 0 := by
  rw [repr_self, Finsupp.single_apply]

theorem _root_.CompressionEvalSignatureMaterialized {«ι» : Type u_1} {«R» : Type u_3} {«M» : Type u_6} [«inst» : Semiring.{u_3} R] [«inst_1» : AddCommMonoid.{u_6} M] [«inst_2» : Module.{u_3, u_6} R M] («b» : Module.Basis.{u_1, u_3, u_6} ι R M) («i» : ι) (j) [Decidable (i = j)] : b.repr (b i) j = if i = j then 1 else 0 := by
  rw [repr_self, Finsupp.single_apply]

run_cmd do
  let env ← Lean.getEnv
  let Option.some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let Option.some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
