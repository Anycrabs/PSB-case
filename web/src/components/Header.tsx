import { Icon } from './Icon'

interface Props {
  onMenu: () => void
  onNewDialog: () => void
}

export function Header({ onMenu, onNewDialog }: Props) {
  return (
    <header className="header">
      <button className="icon-btn on-dark" onClick={onMenu} aria-label="Диалоги">
        <Icon name="menu" />
      </button>
      <div className="brand">
        <span className="brand-mark">ПСБ</span>
        <span className="brand-title">Юридический ассистент</span>
      </div>
      <div className="header-spacer" />
      <span className="header-note">Отвечает только по базе документов</span>
      <button className="btn on-dark" onClick={onNewDialog}>
        <Icon name="plus" /> Новый диалог
      </button>
    </header>
  )
}
