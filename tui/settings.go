package main

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

const settingsActionCount = 3

func (m model) renderSettings() string {
	var s strings.Builder
	maxW := m.dashContentWidth()

	s.WriteString(m.renderDivider("Theme"))
	s.WriteString("\n")
	s.WriteString(fmt.Sprintf("  %s  %s    %s to cycle, or press t\n",
		m.styles.SettingsKey.Render("Active:"),
		m.styles.SettingsValue.Render(themes[m.currentThemeIdx].Name),
		m.styles.Dim.Render("\u2190\u2192")))

	if m.health != nil || m.memoriaConfig != nil {
		s.WriteString(m.renderDivider("Memoria"))
		s.WriteString("\n")
	}

	if m.health != nil {
		if maxW > 50 {
			leftCol := fmt.Sprintf("  %s  %s", m.styles.SettingsKey.Render("Version:"), m.styles.SettingsValue.Render(m.health.MemoriaVersion))
			rightCol := fmt.Sprintf("  %s  %s", m.styles.SettingsKey.Render("Sessions:"), m.styles.SettingsValue.Render(fmt.Sprintf("%d", m.health.SessionsIndexed)))
			padding := maxW - lipgloss.Width(leftCol) - lipgloss.Width(rightCol)
			if padding < 2 {
				padding = 2
			}
			s.WriteString(leftCol + strings.Repeat(" ", padding) + rightCol + "\n")
		} else {
			s.WriteString(fmt.Sprintf("  %s  %s\n",
				m.styles.SettingsKey.Render("Version:"),
				m.styles.SettingsValue.Render(m.health.MemoriaVersion)))
			s.WriteString(fmt.Sprintf("  %s  %s\n",
				m.styles.SettingsKey.Render("Sessions:"),
				m.styles.SettingsValue.Render(fmt.Sprintf("%d", m.health.SessionsIndexed))))
		}
	}
	if m.memoriaConfig != nil {
		cfg := m.memoriaConfig
		if maxW > 50 {
			pairs := []struct{ k, v string }{
				{"Poll:", fmt.Sprintf("%ds", cfg.PollInterval)},
				{"Timeout:", fmt.Sprintf("%ds", cfg.AgentStaleSec)},
				{"Mem limit:", fmt.Sprintf("%d", cfg.MemoryLimit)},
				{"Consolidate:", fmt.Sprintf("%d", cfg.ChitchatConsolidateThreshold)},
				{"Chat poll:", fmt.Sprintf("%ds", cfg.ChitchatPollInterval)},
				{"Max msgs:", fmt.Sprintf("%d", cfg.ChitchatMaxMessages)},
				{"Sleep:", fmt.Sprintf("%dh", cfg.SleepCycleHours)},
				{"Max sessions:", fmt.Sprintf("%d", cfg.SessionMaxRecords)},
				{"Auto-accept:", fmt.Sprintf("%d", cfg.AutoAcceptThreshold)},
			}
			for i := 0; i < len(pairs); i += 2 {
				left := fmt.Sprintf("  %s  %s", m.styles.SettingsKey.Render(pairs[i].k), m.styles.SettingsValue.Render(pairs[i].v))
				if i+1 < len(pairs) {
					right := fmt.Sprintf("  %s  %s", m.styles.SettingsKey.Render(pairs[i+1].k), m.styles.SettingsValue.Render(pairs[i+1].v))
					padding := maxW - lipgloss.Width(left) - lipgloss.Width(right)
					if padding < 2 {
						padding = 2
					}
					s.WriteString(left + strings.Repeat(" ", padding) + right + "\n")
				} else {
					s.WriteString(left + "\n")
				}
			}
		} else {
			s.WriteString(fmt.Sprintf("  %s  %ds\n", m.styles.SettingsKey.Render("Poll:"), cfg.PollInterval))
			s.WriteString(fmt.Sprintf("  %s  %ds\n", m.styles.SettingsKey.Render("Timeout:"), cfg.AgentStaleSec))
			s.WriteString(fmt.Sprintf("  %s  %d\n", m.styles.SettingsKey.Render("Mem limit:"), cfg.MemoryLimit))
			s.WriteString(fmt.Sprintf("  %s  %ds\n", m.styles.SettingsKey.Render("Chat poll:"), cfg.ChitchatPollInterval))
			s.WriteString(fmt.Sprintf("  %s  %d\n", m.styles.SettingsKey.Render("Consolidate:"), cfg.ChitchatConsolidateThreshold))
			s.WriteString(fmt.Sprintf("  %s  %d\n", m.styles.SettingsKey.Render("Max msgs:"), cfg.ChitchatMaxMessages))
			s.WriteString(fmt.Sprintf("  %s  %dh\n", m.styles.SettingsKey.Render("Sleep:"), cfg.SleepCycleHours))
			s.WriteString(fmt.Sprintf("  %s  %d\n", m.styles.SettingsKey.Render("Max sessions:"), cfg.SessionMaxRecords))
			s.WriteString(fmt.Sprintf("  %s  %d\n", m.styles.SettingsKey.Render("Auto-accept:"), cfg.AutoAcceptThreshold))
		}
	}
	if m.health == nil && m.memoriaConfig == nil {
		s.WriteString("  " + m.styles.Dim.Render("unreachable") + "\n")
	}

	s.WriteString(m.renderDivider("Chitchat"))
	s.WriteString("\n")
	roomLine := fmt.Sprintf("  %s  %d", m.styles.SettingsKey.Render("Rooms:"), len(m.rooms))
	if len(m.rooms) > 0 {
		roomLine += fmt.Sprintf("  %s  %s", m.styles.SettingsKey.Render("Active:"), m.styles.SettingsValue.Render(m.rooms[m.activeRoom]))
	}
	s.WriteString(roomLine + "\n")
	s.WriteString(fmt.Sprintf("  %s  %d\n",
		m.styles.SettingsKey.Render("Messages:"),
		m.chitchat.MessageCount("general")))

	s.WriteString(m.renderDivider("Actions"))
	s.WriteString("\n")

	type actionItem struct {
		key   string
		label string
		desc  string
	}
	actions := []actionItem{
		{"c", " RUN ", "Force Consolidate"},
		{"p", " CLEAR ", "Clear Proposals"},
		{"t", " THEME ", "Next Theme"},
	}
	for i, a := range actions {
		btnLabel := a.label
		if m.focusMode == 2 && m.settingsFocusIdx == i {
			btnLabel = m.styles.FocusStyle.Render(a.label)
		} else {
			btnLabel = m.styles.ActionButton.Render(a.label)
		}
		s.WriteString(fmt.Sprintf("  %s  %s  %s\n", btnLabel, a.key, a.desc))
	}

	s.WriteString("\n")
	s.WriteString(m.styles.Dim.Render("\u2191\u2193 navigate  Enter activate  Tab cycle"))

	return s.String()
}