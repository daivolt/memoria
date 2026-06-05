package main

import (
	"fmt"
	"strings"
)

func (m model) renderSettings() string {
	var s strings.Builder

	s.WriteString(m.styles.SettingsHeader.Render("Theme"))
	s.WriteString("\n")
	s.WriteString(fmt.Sprintf("  %s  %s    ← → to cycle, or press t\n",
		m.styles.SettingsKey.Render("Active:"),
		m.styles.SettingsValue.Render(themes[m.currentThemeIdx].Name)))

	s.WriteString(m.styles.SettingsHeader.Render("TUI Polling"))
	s.WriteString("\n")
	s.WriteString(fmt.Sprintf("  %s  %s\n",
		m.styles.SettingsKey.Render("Chat refresh:"),
		m.styles.SettingsValue.Render(roomPollInterval.String())))
	s.WriteString(fmt.Sprintf("  %s  %s\n",
		m.styles.SettingsKey.Render("Memoria refresh:"),
		m.styles.SettingsValue.Render(memoriaPollInterval.String())))

	s.WriteString(m.styles.SettingsHeader.Render("Memoria"))
	s.WriteString("\n")
	if m.health != nil {
		s.WriteString(fmt.Sprintf("  %s  %s\n",
			m.styles.SettingsKey.Render("Version:"),
			m.styles.SettingsValue.Render(m.health.MemoriaVersion)))
		s.WriteString(fmt.Sprintf("  %s  %s\n",
			m.styles.SettingsKey.Render("Sessions:"),
			m.styles.SettingsValue.Render(fmt.Sprintf("%d", m.health.SessionsIndexed))))
	}
	if m.memoriaConfig != nil {
		cfg := m.memoriaConfig
		s.WriteString(fmt.Sprintf("  %s  %ds\n",
			m.styles.SettingsKey.Render("Poll interval:"),
			cfg.PollInterval))
		s.WriteString(fmt.Sprintf("  %s  %ds\n",
			m.styles.SettingsKey.Render("Heartbeat timeout:"),
			cfg.AgentStaleSec))
		s.WriteString(fmt.Sprintf("  %s  %d\n",
			m.styles.SettingsKey.Render("Memory limit:"),
			cfg.MemoryLimit))
		s.WriteString(fmt.Sprintf("  %s  %ds\n",
			m.styles.SettingsKey.Render("Chitchat poll:"),
			cfg.ChitchatPollInterval))
		s.WriteString(fmt.Sprintf("  %s  %d\n",
			m.styles.SettingsKey.Render("Consolidate at:"),
			cfg.ChitchatConsolidateThreshold))
		s.WriteString(fmt.Sprintf("  %s  %d\n",
			m.styles.SettingsKey.Render("Max chat msgs:"),
			cfg.ChitchatMaxMessages))
		s.WriteString(fmt.Sprintf("  %s  %dh\n",
			m.styles.SettingsKey.Render("Sleep cycle:"),
			cfg.SleepCycleHours))
		s.WriteString(fmt.Sprintf("  %s  %d\n",
			m.styles.SettingsKey.Render("Max sessions:"),
			cfg.SessionMaxRecords))
		s.WriteString(fmt.Sprintf("  %s  %d\n",
			m.styles.SettingsKey.Render("Auto-accept:"),
			cfg.AutoAcceptThreshold))
	}
	if m.health == nil && m.memoriaConfig == nil {
		s.WriteString("  " + m.styles.Dim.Render("unreachable") + "\n")
	}

	s.WriteString(m.styles.SettingsHeader.Render("Chitchat"))
	s.WriteString("\n")
	s.WriteString(fmt.Sprintf("  %s  %d\n",
		m.styles.SettingsKey.Render("Rooms:"),
		len(m.rooms)))
	if len(m.rooms) > 0 {
		s.WriteString(fmt.Sprintf("  %s  %s\n",
			m.styles.SettingsKey.Render("Active room:"),
			m.styles.SettingsValue.Render(m.rooms[m.activeRoom])))
	}
	s.WriteString(fmt.Sprintf("  %s  %d\n",
		m.styles.SettingsKey.Render("Polled messages:"),
		m.chitchat.MessageCount("general")))

	s.WriteString(m.styles.SettingsHeader.Render("Actions"))
	s.WriteString("\n")
	btn := m.styles.ActionButton.Render
	s.WriteString(fmt.Sprintf("  %s  c  Force Consolidate Now\n", btn(" RUN ")))
	s.WriteString(fmt.Sprintf("  %s  p  Clear All Proposals\n", btn(" CLEAR ")))
	s.WriteString(fmt.Sprintf("  %s  t  Next Theme\n", btn(" THEME ")))

	s.WriteString("\n")
	s.WriteString(m.styles.Dim.Render("?  show keybindings"))

	return s.String()
}
