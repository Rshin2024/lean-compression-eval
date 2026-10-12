

theorem _root_.CompressionEvalSignatureOriginal [Nontrivial R] (hv : LinearIndepOn R v s) : InjOn v s := injOn_iff_injective.2 <| LinearIndependent.injective hv

theorem _root_.CompressionEvalSignatureMaterialized {«ι» : Type u'} {«R» : Type u_2} {«s» : Set.{u'} ι} {«M» : Type u_4} {«v» : ι → M} [«inst» : Semiring.{u_2} R] [«inst_1» : AddCommMonoid.{u_4} M] [«inst_2» : Module.{u_2, u_4} R M] [Nontrivial R] (hv : LinearIndepOn R v s) : InjOn v s := injOn_iff_injective.2 <| LinearIndependent.injective hv

run_cmd do
  let env ← Lean.getEnv
  let Option.some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let Option.some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
