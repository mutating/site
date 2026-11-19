import { Icon } from "../../src/components/UI";
import { machines, type Machine } from "./model";
import { Action, Panel, Status, useWorkspace } from "./parts";

function DeviceResource({ name, amount, total, unit }: { name: string; amount: number; total: number; unit?: string }) {
  return <span className="device-resource">
    <span>{name}</span><b>{amount}/{total}{unit && ` ${unit}`}</b>
    <span className="device-meter" aria-hidden="true"><i style={{ width: `${amount / total * 100}%` }} /></span>
  </span>;
}

function DeviceTile({ machine, compact = false }: { machine: Machine; compact?: boolean }) {
  const { open } = useWorkspace();
  return <button type="button" className={`device-tile${compact ? " device-compact" : ""}`} onClick={() => open({ kind: "computer", id: machine.id })}>
    <span className="device-top"><span className="machine-icon"><Icon name="computer" size={22} /></span><Status online={machine.online} /></span>
    <span className="device-name"><strong>{machine.name}</strong><small>{machine.platform}</small></span>
    <span className="device-details">
      <DeviceResource name="Свободный CPU" amount={machine.cpu} total={machine.cores} />
      {!compact && <DeviceResource name="Свободная память" amount={machine.memory} total={machine.ram} unit="ГиБ" />}
    </span>
  </button>;
}

export function Devices({ compact = false, featured = false }: { compact?: boolean; featured?: boolean }) {
  const { open } = useWorkspace();
  const mainMachine = machines.reduce((current, machine) => machine.cpu > current.cpu ? machine : current);

  return <Panel title="Компьютеры" count={machines.length} section="machines" className="device-panel"
    action={<Action tone="quiet" onClick={() => open({ kind: "computers" })}>Все компьютеры</Action>}>
    {featured ? <div className="device-feature">
      <DeviceTile machine={mainMachine} />
      <div>{machines.filter((machine) => machine.id !== mainMachine.id).map((machine) => <DeviceTile key={machine.id} machine={machine} compact />)}</div>
    </div> : <div className="device-grid">{machines.map((machine) => <DeviceTile key={machine.id} machine={machine} compact={compact} />)}</div>}
  </Panel>;
}
