package main

import "github.com/charmbracelet/lipgloss"

type Theme struct {
	Name            string
	Base, Mantle    lipgloss.TerminalColor
	Text, Subtext0  lipgloss.TerminalColor
	Overlay0        lipgloss.TerminalColor
	Blue, Green     lipgloss.TerminalColor
	Yellow, Red     lipgloss.TerminalColor
	Mauve, Teal     lipgloss.TerminalColor
	Peach           lipgloss.TerminalColor
}

func c(hex string) lipgloss.TerminalColor {
	return lipgloss.Color(hex)
}

var CatppuccinMocha = Theme{
	Name:     "Catppuccin Mocha",
	Base:     c("#1e1e2e"),
	Mantle:   c("#181825"),
	Text:     c("#cdd6f4"),
	Subtext0: c("#a6adc8"),
	Overlay0: c("#6c7086"),
	Blue:     c("#89b4fa"),
	Green:    c("#a6e3a1"),
	Yellow:   c("#f9e2af"),
	Red:      c("#f38ba8"),
	Mauve:    c("#cba6f7"),
	Teal:     c("#94e2d5"),
	Peach:    c("#fab387"),
}

var Nord = Theme{
	Name:     "Nord",
	Base:     c("#2e3440"),
	Mantle:   c("#3b4252"),
	Text:     c("#eceff4"),
	Subtext0: c("#d8dee9"),
	Overlay0: c("#616e88"),
	Blue:     c("#81a1c1"),
	Green:    c("#a3be8c"),
	Yellow:   c("#ebcb8b"),
	Red:      c("#bf616a"),
	Mauve:    c("#b48ead"),
	Teal:     c("#88c0d0"),
	Peach:    c("#d08770"),
}

var Cyberpunk = Theme{
	Name:     "Cyberpunk",
	Base:     c("#0a0a1a"),
	Mantle:   c("#12122a"),
	Text:     c("#e0e0ff"),
	Subtext0: c("#a0a0cc"),
	Overlay0: c("#505080"),
	Blue:     c("#00aaff"),
	Green:    c("#00ffaa"),
	Yellow:   c("#ffaa00"),
	Red:      c("#ff3355"),
	Mauve:    c("#ff00ff"),
	Teal:     c("#00ffcc"),
	Peach:    c("#ff6600"),
}

var Gruvbox = Theme{
	Name:     "Gruvbox Dark",
	Base:     c("#282828"),
	Mantle:   c("#1d2021"),
	Text:     c("#ebdbb2"),
	Subtext0: c("#d5c4a1"),
	Overlay0: c("#928374"),
	Blue:     c("#83a598"),
	Green:    c("#b8bb26"),
	Yellow:   c("#fabd2f"),
	Red:      c("#fb4934"),
	Mauve:    c("#d3869b"),
	Teal:     c("#8ec07c"),
	Peach:    c("#fe8019"),
}

var themes = []Theme{CatppuccinMocha, Nord, Cyberpunk, Gruvbox}

type StyleBundle struct {
	LeftPane  lipgloss.Style
	RightPane lipgloss.Style

	StatusBar lipgloss.Style
	Input     lipgloss.Style

	Tab       lipgloss.Style
	ActiveTab lipgloss.Style

	RoomTab       lipgloss.Style
	ActiveRoomTab lipgloss.Style

	Info    lipgloss.Style
	Dim     lipgloss.Style
	Error   lipgloss.Style
	Success lipgloss.Style

	Spinner lipgloss.Style
	Help    lipgloss.Style
	Prompt  lipgloss.Style

	UserMsg   lipgloss.Style
	AgentMsg  lipgloss.Style
	SystemMsg lipgloss.Style
	Timestamp lipgloss.Style

	DotGreen  string
	DotYellow string
	DotRed    string
	DotDim    string

	SettingsHeader lipgloss.Style
	SettingsValue  lipgloss.Style
	SettingsKey    lipgloss.Style
	ActionButton   lipgloss.Style
}

func NewStyleBundle(t Theme) StyleBundle {
	base := t.Base
	mantle := t.Mantle
	text := t.Text
	sub := t.Subtext0
	overlay := t.Overlay0
	blue := t.Blue
	green := t.Green
	yellow := t.Yellow
	red := t.Red
	mauve := t.Mauve
	teal := t.Teal
	peach := t.Peach

	dot := func(c lipgloss.TerminalColor) string {
		return lipgloss.NewStyle().Foreground(c).Render("●")
	}

	return StyleBundle{
		LeftPane: lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(blue).
			Padding(0, 1),

		RightPane: lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(blue).
			Padding(0, 1),

		StatusBar: lipgloss.NewStyle().
			Foreground(text).
			Background(mantle).
			Padding(0, 1).
			Align(lipgloss.Left),

		Input: lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(overlay).
			Padding(0, 1),

		Tab: lipgloss.NewStyle().
			Padding(0, 2).
			Foreground(overlay),

		ActiveTab: lipgloss.NewStyle().
			Padding(0, 2).
			Foreground(text).
			Background(blue).
			Bold(true),

		RoomTab: lipgloss.NewStyle().
			Padding(0, 2).
			Foreground(overlay),

		ActiveRoomTab: lipgloss.NewStyle().
			Padding(0, 2).
			Foreground(text).
			Background(blue).
			Bold(true),

		Info:    lipgloss.NewStyle().Foreground(sub).Padding(0, 1),
		Dim:     lipgloss.NewStyle().Foreground(overlay),
		Error:   lipgloss.NewStyle().Foreground(red),
		Success: lipgloss.NewStyle().Foreground(green),

		Spinner: lipgloss.NewStyle().Foreground(mauve),
		Help:    lipgloss.NewStyle().Foreground(overlay).Padding(0, 1),
		Prompt:  lipgloss.NewStyle().Foreground(green).Bold(true),

		UserMsg:   lipgloss.NewStyle().Foreground(green).Bold(true),
		AgentMsg:  lipgloss.NewStyle().Foreground(mauve).Bold(true),
		SystemMsg: lipgloss.NewStyle().Foreground(teal).Bold(true),
		Timestamp: lipgloss.NewStyle().Foreground(overlay),

		DotGreen:  dot(green),
		DotYellow: dot(yellow),
		DotRed:    dot(red),
		DotDim:    dot(overlay),

		SettingsHeader: lipgloss.NewStyle().
			Foreground(peach).
			Bold(true).
			Padding(0, 1).
			MarginTop(1),
		SettingsValue: lipgloss.NewStyle().Foreground(text).Padding(0, 1),
		SettingsKey:   lipgloss.NewStyle().Foreground(sub).Padding(0, 1),
		ActionButton: lipgloss.NewStyle().
			Foreground(base).
			Background(blue).
			Bold(true).
			Padding(0, 2),
	}
}
